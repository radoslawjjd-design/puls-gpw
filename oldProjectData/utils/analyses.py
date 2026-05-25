"""
Wspólna logika budowania truncated JSON analiz dla Gemini prompts.

Używane przez:
  - agents/summary_agent.py  (podsumowania dzienne/tygodniowe)
  - agents/watchlist_agent.py (tygodniowe picks)
"""
import json
import logging

logger = logging.getLogger(__name__)

# Domyślne limity (mogą być nadpisane per moduł)
DEFAULT_MAX_CHARS = 80_000
DEFAULT_MAX_ITEMS = 200

# Mapowanie: nazwa pola w output → ścieżka w surowej analizie.
# Paths dopasowane do flat dict zwracanego przez _normalize_bq_analysis
# (po migracji Drive→BQ 2026-04-01 — analizy nie są już zagnieżdżone w "meta").
_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "spolka":          ("_company",),
    "data":            ("data",),
    "typ":             ("typ_ogloszenia",),
    "temat":           ("temat",),
    "sentiment":       ("sentiment",),
    "waga":            ("waga_informacji",),
    "wplyw_na_kurs":   ("wplyw_na_kurs",),
    "kluczowe_fakty":  ("kluczowe_fakty",),
    "podsumowanie":    ("podsumowanie",),
    "rekomendacja":    ("rekomendacja_dzialania",),
    "wplyw_na_wyniki": ("wplyw_na_wyniki",),
    "szacowany_wplyw": ("szacowany_wplyw_finansowy",),
    "ryzyka":          ("ryzyka",),
    "szanse":          ("szanse",),
}

# Pola które domyślnie są pustymi listami gdy brak wartości.
# Pozostałe scalar pola zwracają None i są filtrowane z JSON outputu.
_LIST_FIELDS = {"kluczowe_fakty", "ryzyka", "szanse"}


def _extract_field(analysis: dict, field_name: str) -> object:
    """Wyciąga wartość z analizy po ścieżce zdefiniowanej w _FIELD_MAP.

    Returns:
        - [] dla list fields gdy brak wartości
        - None dla scalar fields gdy brak wartości (filtrowane z JSON)
        - wartość w przeciwnym razie
    """
    path = _FIELD_MAP.get(field_name)
    if not path:
        return None

    obj = analysis
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return [] if field_name in _LIST_FIELDS else None
        if obj is None:
            return [] if field_name in _LIST_FIELDS else None

    return obj


def build_truncated_analyses_json(
    analyses: list[dict],
    fields: list[str],
    max_chars: int = DEFAULT_MAX_CHARS,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> str:
    """
    Buduje kompaktowy JSON analiz do przekazania do Gemini prompt.

    Args:
        analyses:  surowe analizy z Drive (z polami _company, meta, sentiment itd.)
        fields:    lista nazw pól do uwzględnienia (np. ["spolka", "temat", "sentiment"])
        max_chars: max znaków wynikowego JSON
        max_items: max elementów po truncation

    Returns:
        JSON string z listą uproszczonych analiz.
    """
    # Filtrujemy scalar None z output — żeby prompt Gemini nie zawierał pól
    # ze śmieciowymi wartościami (wcześniej literalne "?" dla general-mode).
    simplified = [
        {
            field: value
            for field in fields
            if (value := _extract_field(a, field)) is not None
        }
        for a in analyses
    ]

    result = json.dumps(simplified, ensure_ascii=False, indent=2)

    if len(result) > max_chars:
        high = [a for a in simplified if a.get("waga") == "wysoka"]
        rest = [a for a in simplified if a.get("waga") != "wysoka"]
        trimmed = high + rest[:max(0, max_items - len(high))]
        result = json.dumps(trimmed, ensure_ascii=False, indent=2)
        logger.info(f"Przycięto analizy do {len(trimmed)} (z {len(simplified)})")

    return result
