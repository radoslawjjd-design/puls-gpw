"""Gemini-powered analysis pipeline for ESPI/EBI announcements."""
import json
import logging
import os
import threading
from dataclasses import dataclass

import google.genai as genai

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

_ANALYSIS_SYSTEM_PROMPT = """\
Jesteś analitykiem komunikatów ESPI/EBI spółek notowanych na GPW i NewConnect.
Twoim zadaniem jest wyciągnięcie kluczowych informacji z komunikatu giełdowego.

Zwróć JSON z polami:
- company: pełna nazwa spółki (string)
- ticker: symbol giełdowy (string, np. "PKO", "CDR")
- event_type: typ zdarzenia (string, jedna z wartości z listy poniżej)
- key_numbers: lista kluczowych liczb/kwot z komunikatu, sformatowanych czytelnie (array of strings)
- sentiment: ocena wydźwięku (string: "positive", "negative", "neutral")
- summary_pl: krótkie podsumowanie komunikatu po polsku, max 2 zdania (string)

Dozwolone wartości event_type:
wyniki_finansowe, upadlosc, restrukturyzacja, przejecie, fuzja, wezwanie,
dywidenda, emisja_akcji, kontrakt_znaczacy, transakcja_insiderow,
wyniki_sprzedazowe, skup_akcji, zmiana_zarzadu, compliance, inne

Jeśli nie możesz określić event_type — użyj "inne".
Liczby formatuj czytelnie: zamiast "120 100 000 PLN" pisz "120,1 mln PLN".\
"""

_GATE_SYSTEM_PROMPT = """\
Jesteś audytorem analiz finansowych. Weryfikujesz czy analiza komunikatu giełdowego
jest zgodna z jego oryginalną treścią.

Sprawdź:
1. Czy liczby i kwoty w polu key_numbers mają odpowiedniki w oryginalnej treści?
   WAŻNE: Liczby mogą być sformatowane inaczej (np. "120 100 000 PLN" w tekście
   = "120,1 mln PLN" w analizie) — to jest POPRAWNE. Odrzuć tylko jeśli liczba
   z analizy nie ma żadnego odpowiednika w źródle lub rząd wielkości jest wyraźnie
   błędny (np. "120 mln" zamiast "12 mln").
2. Czy company i ticker są zgodne z treścią komunikatu?
3. Czy summary_pl jest spójne z treścią?

Zwróć JSON:
{"approved": true, "reason": null}
lub
{"approved": false, "reason": "krótkie wyjaśnienie co jest niezgodne"}\
"""

_genai_client: genai.Client | None = None
_genai_lock = threading.Lock()


def _get_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        with _genai_lock:
            if _genai_client is None:
                _genai_client = genai.Client(
                    vertexai=True,
                    project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
                    location=os.environ.get("GOOGLE_CLOUD_REGION", "europe-central2"),
                )
    return _genai_client


@dataclass
class AnalysisResult:
    announcement_id: str
    structured_analysis: str | None
    analysis_approved: bool | None
    analysis_reject_reason: str | None
    event_type: str | None
    analysis_score: float | None


def _call_analysis(parsed_content: str) -> dict | None:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    try:
        client = _get_client()
        response = client.models.generate_content(
            model=model,
            contents=parsed_content,
            config=genai.types.GenerateContentConfig(
                system_instruction=_ANALYSIS_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )
        return json.loads(response.text)
    except Exception:
        logger.warning("Gemini analysis call failed", exc_info=True)
        return None


def _call_gate(parsed_content: str, structured_analysis: str) -> tuple[bool | None, str | None]:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    user_message = f"TREŚĆ KOMUNIKATU:\n{parsed_content}\n\nANALIZA:\n{structured_analysis}"
    try:
        client = _get_client()
        response = client.models.generate_content(
            model=model,
            contents=user_message,
            config=genai.types.GenerateContentConfig(
                system_instruction=_GATE_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )
        data = json.loads(response.text)
        return bool(data["approved"]), data.get("reason")
    except Exception:
        logger.warning("Gemini gate call failed", exc_info=True)
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

    analysis_dict = _call_analysis(parsed_content)
    if analysis_dict is None:
        logger.warning("Analyzer: analysis call failed for %s — skipping", announcement_id)
        return null_result

    raw_event_type = analysis_dict.get("event_type")
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
