"""Gemini-powered analysis pipeline for ESPI/EBI announcements."""
import json
import logging
import time
from dataclasses import dataclass

import google.genai as genai
import json5
from google.genai import errors as _genai_errors
from pydantic import field_validator
from pydantic import BaseModel, ConfigDict, ValidationError

from src.gemini_client import get_client as _get_client, GEMINI_MODEL as _GEMINI_MODEL

# On 429 RESOURCE_EXHAUSTED, retry once after _RETRY_DELAY_S seconds before
# giving up — recovers from short quota windows without blocking the pipeline.
_RETRY_DELAY_S = 30

logger = logging.getLogger(__name__)

_VALID_EVENT_TYPES = {
    "wyniki_finansowe", "upadlosc", "restrukturyzacja", "przejecie", "fuzja",
    "wezwanie", "dywidenda", "emisja_akcji", "kontrakt_znaczacy",
    "transakcja_insiderow", "wyniki_sprzedazowe", "skup_akcji",
    "zmiana_zarzadu", "compliance", "inne",
}

_TIER1 = {"DGN", "ELT", "SNT", "TOA", "VOT", "XTB", "PAS", "KRU", "LBW", "APT"}
_TIER2 = {
    "PKO", "KGH", "PKN", "PGE", "PZU", "CDR", "KTY", "LPP", "DNP", "ZAB",
    "PEO", "ASB", "CBF", "DVL", "CRI", "DEK",
}
_TIER3 = {"MDV", "ALR", "TPE", "MBK", "ALE", "PCO", "BDX"}

_TIER_BONUS = {**{t: 40 for t in _TIER1}, **{t: 25 for t in _TIER2}, **{t: 10 for t in _TIER3}}

_EVENT_TYPE_SCORES = {
    "wyniki_finansowe": 100, "upadlosc": 95, "restrukturyzacja": 95,
    "przejecie": 90, "fuzja": 90, "wezwanie": 90,
    "dywidenda": 85, "emisja_akcji": 80, "kontrakt_znaczacy": 75,
    "transakcja_insiderow": 65, "wyniki_sprzedazowe": 60, "skup_akcji": 55,
    "zmiana_zarzadu": 50, "compliance": 20, "inne": 20,
}

_VALID_SENTIMENTS = {"pozytywny", "negatywny", "neutralny"}


class _AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_type: str
    sentiment: str = "neutralny"
    key_numbers: list[str]
    summary_pl: str

    @field_validator("sentiment", mode="before")
    @classmethod
    def _coerce_sentiment(cls, v: object) -> str:
        return v if v in _VALID_SENTIMENTS else "neutralny"


_ANALYSIS_SYSTEM_PROMPT = """\
Jesteś analitykiem komunikatów ESPI/EBI spółek notowanych na GPW i NewConnect.
Twoim zadaniem jest wyciągnięcie kluczowych informacji z komunikatu giełdowego.

Ticker i nazwa spółki są już znane — NIE wyciągaj ich z tekstu.

Zwróć JSON z polami:
- event_type: typ zdarzenia (string, jedna z wartości z listy poniżej)
- sentiment: ogólny wydźwięk komunikatu dla inwestora (string, jedna z wartości: pozytywny, negatywny, neutralny)
- key_numbers: lista kluczowych liczb/kwot (array of strings) — patrz zasady poniżej
- summary_pl: krótkie podsumowanie komunikatu po polsku, max 2 zdania (string).
  WAŻNE: opieraj się WYŁĄCZNIE na treści komunikatu — nie dodawaj kontekstu
  ani ocen których nie ma w tekście.

Język: wszystkie pola (key_numbers i summary_pl) pisz po polsku — jeśli komunikat jest po angielsku lub innym języku, przetłumacz.

Dozwolone wartości event_type:
wyniki_finansowe, upadlosc, restrukturyzacja, przejecie, fuzja, wezwanie,
dywidenda, emisja_akcji, kontrakt_znaczacy, transakcja_insiderow,
wyniki_sprzedazowe, skup_akcji, zmiana_zarzadu, compliance, inne

Jeśli nie możesz określić event_type — użyj "inne".
UWAGA zmiana_zarzadu: dotyczy WYŁĄCZNIE zmian w Zarządzie (Management Board).
Zmiany w Radzie Nadzorczej (Supervisory Board) → użyj "inne".
Liczby formatuj czytelnie: zamiast "120 100 000 PLN" pisz "120,1 mln PLN".

=== ZASADY key_numbers — PRIORYTET KWOT ===

Zawsze wyciągaj liczby które mają REALNY WPŁYW NA WYCENĘ spółki. Puste pozycje
lepsze niż zmyślone. Jeśli zmiana r/r jest dostępna — ZAWSZE ją dołącz.

wyniki_finansowe:
  Priorytet 1: Przychody ze sprzedaży + zmiana r/r (np. "Przychody: 136,9 mln PLN (+18% r/r)")
  Priorytet 2: Zysk netto + zmiana r/r
  Priorytet 3: EBITDA lub zysk operacyjny + zmiana r/r (jeśli podane)
  Priorytet 4: Marża netto lub EBITDA (jeśli podana)

wyniki_sprzedazowe:
  Priorytet 1: Wolumen sprzedaży lub przychody + zmiana r/r
  Priorytet 2: Kluczowy segment/produkt z kwotą i zmianą r/r

dywidenda:
  Priorytet 1: Dywidenda na akcję (DPS) w PLN
  Priorytet 2: Łączna kwota dywidendy
  Priorytet 3: Stopa dywidendy (% ceny akcji) — jeśli podana
  Priorytet 4: Jaki % zysku netto stanowi (payout ratio) — jeśli wynika z komunikatu

kontrakt_znaczacy / przejecie / fuzja:
  Priorytet 1: Wartość transakcji/kontraktu
  Priorytet 2: Okres obowiązywania lub harmonogram płatności (jeśli istotny)

wezwanie:
  Priorytet 1: Cena za akcję w wezwaniu
  Priorytet 2: Łączna wartość wezwania lub % pakietu docelowego

emisja_akcji / skup_akcji:
  Priorytet 1: Liczba akcji i cena emisji/skupu
  Priorytet 2: Łączna wartość emisji/skupu
  Priorytet 3: % rozwodnienia lub % kapitału (jeśli podany)

transakcja_insiderow:
  Priorytet 1: Kwota transakcji
  Priorytet 2: Liczba akcji i cena jednostkowa

zmiana_zarzadu / compliance:
  key_numbers = [] — te komunikaty rzadko zawierają istotne liczby finansowe.
  Wyjątek: wymierne kwoty explicite podane w tekście (np. odprawa, kara regulacyjna).

Dla pozostałych event_type: wyciągnij maksymalnie 3 najważniejsze kwoty/liczby.\
"""

_GATE_SYSTEM_PROMPT = """\
Jesteś audytorem analiz finansowych. Weryfikujesz czy liczby z analizy
komunikatu giełdowego są zgodne z jego oryginalną treścią.

Sprawdź TYLKO:
Czy liczby i kwoty w polu key_numbers mają odpowiedniki w oryginalnej treści?
  WAŻNE: Liczby mogą być sformatowane inaczej (np. "120 100 000 PLN" w tekście
  = "120,1 mln PLN" w analizie) — to jest POPRAWNE. Odrzuć tylko jeśli liczba
  z analizy nie ma żadnego odpowiednika w źródle lub rząd wielkości jest wyraźnie
  błędny (np. "120 mln" zamiast "12 mln").

Jeśli key_numbers jest pustą listą — zatwierdź (nie ma liczb do weryfikacji).

NIE weryfikuj: summary_pl, event_type, tickera, nazwy spółki.

Zwróć JSON:
{"approved": true, "reason": null}
lub
{"approved": false, "reason": "która liczba jest niezgodna i co jest prawidłową wartością"}\
"""

@dataclass
class AnalysisResult:
    announcement_id: str
    structured_analysis: str | None
    analysis_approved: bool | None
    analysis_reject_reason: str | None
    event_type: str | None
    analysis_score: float | None


def _call_analysis(parsed_content: str) -> dict | None:
    for attempt in range(2):
        try:
            client = _get_client()
            response = client.models.generate_content(
                model=_GEMINI_MODEL,
                contents=parsed_content,
                config=genai.types.GenerateContentConfig(
                    system_instruction=_ANALYSIS_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )
            data = json5.loads(response.text)
            return _AnalysisResponse.model_validate(data).model_dump()
        except ValidationError:
            logger.warning("Gemini analysis schema invalid", exc_info=True)
            return None
        except _genai_errors.ClientError as exc:
            if exc.status_code == 429 and attempt == 0:
                logger.warning("Gemini analysis 429 — backing off %ds before retry", _RETRY_DELAY_S)
                time.sleep(_RETRY_DELAY_S)
                continue
            logger.warning("Gemini analysis call failed", exc_info=True)
            return None
        except Exception:
            logger.warning("Gemini analysis call failed", exc_info=True)
            return None
    return None


def _call_gate(parsed_content: str, structured_analysis: str) -> tuple[bool | None, str | None]:
    user_message = f"TREŚĆ KOMUNIKATU:\n{parsed_content}\n\nANALIZA:\n{structured_analysis}"
    for attempt in range(2):
        try:
            client = _get_client()
            response = client.models.generate_content(
                model=_GEMINI_MODEL,
                contents=user_message,
                config=genai.types.GenerateContentConfig(
                    system_instruction=_GATE_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )
            data = json5.loads(response.text)
            return bool(data["approved"]), data.get("reason")
        except _genai_errors.ClientError as exc:
            if exc.status_code == 429 and attempt == 0:
                logger.warning("Gemini gate 429 — backing off %ds before retry", _RETRY_DELAY_S)
                time.sleep(_RETRY_DELAY_S)
                continue
            logger.warning("Gemini gate call failed", exc_info=True)
            return None, None
        except Exception:
            logger.warning("Gemini gate call failed", exc_info=True)
            return None, None
    return None, None


def _compute_score(
    event_type: str | None,
    ticker: str | None,
    priority: str | None,
) -> float:
    tier_bonus = _TIER_BONUS.get(ticker, 0) if ticker else 0
    event_score = _EVENT_TYPE_SCORES.get(event_type or "inne", _EVENT_TYPE_SCORES["inne"])
    priority_bonus = 20 if priority == "Ważny" else 0
    return float(tier_bonus + event_score + priority_bonus)


def analyze_announcement(
    announcement_id: str,
    parsed_content: str | None,
    ticker: str | None,
    priority: str | None,
) -> AnalysisResult:
    """Orchestrate analysis, gate verification, and scoring for one announcement.

    Never raises — all errors result in NULL fields and a WARNING log.
    """
    null_result = AnalysisResult(
        announcement_id=announcement_id,
        structured_analysis=None,
        analysis_approved=None,
        analysis_reject_reason=None,
        event_type=None,
        analysis_score=None,
    )

    if not parsed_content:
        logger.info("Analyzer: skip %s — no parsed_content", announcement_id)
        return null_result

    if not ticker:
        logger.info("Analyzer: skip %s — no ticker", announcement_id)
        return null_result

    analysis_dict = _call_analysis(parsed_content)
    if analysis_dict is None:
        logger.warning("Analyzer: analysis call failed for %s — skipping", announcement_id)
        return null_result

    raw_event_type = analysis_dict.get("event_type")
    if raw_event_type not in _VALID_EVENT_TYPES:
        logger.warning(
            "Analyzer: unknown event_type %r for %s — falling back to 'inne'",
            raw_event_type, announcement_id,
        )
    event_type = raw_event_type if raw_event_type in _VALID_EVENT_TYPES else "inne"

    structured_analysis = json.dumps(analysis_dict, ensure_ascii=False)

    approved, reason = _call_gate(parsed_content, structured_analysis)
    if approved is None:
        logger.warning("Analyzer: gate call failed for %s — partial result", announcement_id)
        return AnalysisResult(
            announcement_id=announcement_id,
            structured_analysis=structured_analysis,
            analysis_approved=None,
            analysis_reject_reason=None,
            event_type=event_type,
            analysis_score=None,
        )

    score = _compute_score(event_type, ticker, priority) if approved else None

    logger.info(
        "Analyzer: %s event_type=%s approved=%s score=%s",
        announcement_id, event_type, approved, score,
    )
    return AnalysisResult(
        announcement_id=announcement_id,
        structured_analysis=structured_analysis,
        analysis_approved=approved,
        analysis_reject_reason=reason if not approved else None,
        event_type=event_type,
        analysis_score=score,
    )
