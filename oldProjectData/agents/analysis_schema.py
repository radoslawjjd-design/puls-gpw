"""
Pydantic schema dla odpowiedzi Gemini z analizy ogłoszeń ESPI/EBI.

Używane przez `agents/analysis_agent.py` po `json.loads()` aby walidować
strukturę PRZED zwróceniem do konsumentów (digest, broker, summary).

Bez tej walidacji KeyError/TypeError pojawiały się daleko od źródła
(np. w `digest.py:191` gdy `waga_informacji` brakowało) i powodowały
trudne do diagnozy crashe pipeline'u.

Strategy:
- extra="allow" — agent dorzuca pola `_analysis_mode`, `_has_macro_context`,
  `_has_profile_context` po walidacji; muszą być zachowane.
- Pola Optional dla portfolio mode (deeper analysis).
- Pola enum przechodzą przez `_normalize_enum()` (BeforeValidator) który:
  1. lowercase + strip whitespace
  2. usuwa polskie diakrytyki ("średnia" → "srednia")
  3. jeśli niedopasowane do enum — fallback do bezpiecznej default + log WARNING
     z RAW value dla diagnostyki (bez crashowania całej analizy)

Fix #11 (2026-04-23): Sentry literal_error dla RANKPROGR/CLNPHARMA — Gemini
zwracał wariacje case/diakrytyk; hard reject tracił prawdziwe analizy.
"""
from __future__ import annotations

import logging
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


# ── Enum types + valid sets ───────────────────────────────────────────────────

SentimentT = Literal["pozytywny", "negatywny", "neutralny"]
WagaT      = Literal["wysoka", "srednia", "niska"]
WplywKursT = Literal["wzrostowy", "spadkowy", "neutralny", "niepewny"]
WplywWynT  = Literal["pozytywny", "negatywny", "neutralny", "brak_danych"]
RekomT     = Literal["trzymaj", "obserwuj", "rozważ_zwiększenie", "rozważ_zmniejszenie"]
PilnoscT   = Literal["natychmiastowa", "do_sledzenia", "informacyjna"]

# Valid sets (do normalizacji) + fallback per pole
_SENTIMENT_VALID    = {"pozytywny", "negatywny", "neutralny"}
_WAGA_VALID         = {"wysoka", "srednia", "niska"}
_WPLYW_KURS_VALID   = {"wzrostowy", "spadkowy", "neutralny", "niepewny"}
_WPLYW_WYN_VALID    = {"pozytywny", "negatywny", "neutralny", "brak_danych"}
_PILNOSC_VALID      = {"natychmiastowa", "do_sledzenia", "informacyjna"}

# Fallback (defensywne defaulty gdy Gemini zwróci coś nierozpoznanego)
_FALLBACK = {
    "sentiment":       "neutralny",
    "waga_informacji": "niska",
    "wplyw_na_kurs":   "neutralny",
    "wplyw_na_wyniki": "neutralny",
    "pilnosc":         "informacyjna",
}


def _strip_diacritics(text: str) -> str:
    """'średnia' → 'srednia'. NFKD decompose + drop combining chars."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _make_normalizer(field_name: str, valid_set: set[str]):
    """Tworzy BeforeValidator który normalizuje case/diacritics i fallbackuje."""
    def _normalize(v: Any) -> str:
        if v is None:
            # Optional fields — pozwól None przejść (Pydantic obsłuży jeśli pole Optional)
            return v  # type: ignore[return-value]
        if not isinstance(v, str):
            # Wartość nie-stringowa → zwracamy raw (Pydantic rzuci typowy ValidationError)
            return v
        normalized = _strip_diacritics(v.strip().lower())
        if normalized in valid_set:
            return normalized
        # Fallback: log RAW value + zwróć bezpieczną default
        fallback = _FALLBACK.get(field_name, "neutralny")
        logger.warning(
            f"Pydantic enum normalize fallback dla pola '{field_name}': "
            f"raw={v!r} → fallback={fallback!r} "
            f"(valid: {sorted(valid_set)})"
        )
        return fallback
    return BeforeValidator(_normalize)


# Annotated typy z normalizatorem
SentimentN  = Annotated[SentimentT, _make_normalizer("sentiment",       _SENTIMENT_VALID)]
WagaN       = Annotated[WagaT,      _make_normalizer("waga_informacji", _WAGA_VALID)]
WplywKursN  = Annotated[WplywKursT, _make_normalizer("wplyw_na_kurs",   _WPLYW_KURS_VALID)]
WplywWynN   = Annotated[WplywWynT,  _make_normalizer("wplyw_na_wyniki", _WPLYW_WYN_VALID)]
PilnoscN    = Annotated[PilnoscT,   _make_normalizer("pilnosc",         _PILNOSC_VALID)]


class AnalysisResponse(BaseModel):
    """Odpowiedź Gemini z analizy ogłoszenia.

    Pola wymagane (oba tryby — general + portfolio):
      typ_ogloszenia, temat, sentiment, kluczowe_fakty,
      wplyw_na_kurs, podsumowanie, waga_informacji

    Pola opcjonalne (głównie portfolio mode):
      uzasadnienie_sentimentu, wplyw_na_wyniki, szacowany_wplyw_finansowy,
      ryzyka, szanse, rekomendacja_dzialania, uzasadnienie_rekomendacji,
      kluczowy_cytat, pilnosc

    extra="allow" — agent dorzuca później pola wewnętrzne (_company,
    _analysis_mode, _has_macro_context, _has_profile_context).
    """

    model_config = ConfigDict(extra="allow")

    # Required (oba tryby) — pola enum przez normalizator
    typ_ogloszenia:    str
    temat:             str
    sentiment:         SentimentN
    kluczowe_fakty:    list[str]
    wplyw_na_kurs:     WplywKursN
    podsumowanie:      str
    waga_informacji:   WagaN

    # Optional (głównie portfolio mode + general "kluczowy_cytat")
    uzasadnienie_sentimentu:   str | None         = None
    kluczowy_cytat:            str | None         = None
    wplyw_na_wyniki:           WplywWynN | None   = None
    szacowany_wplyw_finansowy: str | None         = None
    ryzyka:                    list[str] | None   = Field(default=None)
    szanse:                    list[str] | None   = Field(default=None)
    # rekomendacja_dzialania zostaje bez normalizatora (rzadki, bardziej tolerancja po prostu None)
    rekomendacja_dzialania:    RekomT | None      = None
    uzasadnienie_rekomendacji: str | None         = None
    pilnosc:                   PilnoscN | None    = None


def validate_analysis_dict(data: dict, company: str = "?") -> dict | None:
    """Waliduje dict przeciwko AnalysisResponse i zwraca dict (lub None on fail).

    Args:
        data: surowy dict z `json.loads(gemini_response)`
        company: ticker/nazwa do logu (kontekst diagnostyczny)

    Returns:
        Zwalidowany dict (zachowuje extra fields) lub None gdy schema invalid.
        Nie raise'uje — log + None, żeby agent mógł skipnąć ogłoszenie.
        Pola enum są normalizowane (case/diacritics) lub fallbackują z WARNING.
    """
    try:
        validated = AnalysisResponse.model_validate(data)
        return validated.model_dump(exclude_none=False)
    except ValidationError as e:
        # Skondensowany log: pokaż tylko nazwy pól + typ błędu (nie cały JSON)
        errors = [f"{'.'.join(str(x) for x in err['loc'])}={err['type']}" for err in e.errors()[:5]]
        logger.error(
            f"Pydantic schema invalid dla analizy ({company}): {', '.join(errors)}"
            f"{' (...więcej)' if len(e.errors()) > 5 else ''}"
        )
        return None
