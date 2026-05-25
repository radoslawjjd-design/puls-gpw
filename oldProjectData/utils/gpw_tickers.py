"""
Whitelist tickerów GPW (Faza 3 redesignu X).

Źródło: data/company_list.json (format {ticker: "MNG.PL", nazwa: ...}).
Whitelist zwraca kody **bez suffixu `.PL`**, UPPERCASE — gotowe do użycia
jako cashtag w postach X.

Używane w:
- agents/xpost_compliance.py — walidacja że cashtag to prawdziwy ticker GPW
- agents/x_publisher.py — ostatnia zapora przed publikacją (F6)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_COMPANY_LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "company_list.json"
_DISPLAY_NAMES_PATH = Path(__file__).resolve().parent.parent / "data" / "ticker_display_names.json"


@lru_cache(maxsize=1)
def get_gpw_tickers() -> frozenset[str]:
    """
    Zwraca whitelist tickerów GPW (frozenset dla hashability + immutability).

    Strip `.PL` suffix, UPPERCASE. Np. "MNG.PL" → "MNG", "CDR.PL" → "CDR".

    Cache przez lru_cache — singleton w procesie. Zwraca frozenset bo
    tickery się nie zmieniają podczas runtime (kontrakt z callerami).
    """
    if not _COMPANY_LIST_PATH.exists():
        return frozenset()

    try:
        with open(_COMPANY_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return frozenset()

    tickers: set[str] = set()
    for entry in data.get("spolki", []):
        raw = entry.get("ticker", "").strip().upper()
        if not raw:
            continue
        # Strip `.PL` (format bankier). Inne sufixy zostawiamy (edge case).
        if raw.endswith(".PL"):
            raw = raw[:-3]
        if raw:
            tickers.add(raw)

    return frozenset(tickers)


def is_known_gpw_ticker(ticker: str) -> bool:
    """
    Sprawdza czy podany symbol jest znanym tickerem GPW.

    Akceptuje formy:
    - "CDR", "cdr" (case-insensitive)
    - "$CDR" (opcjonalny dollar prefix)

    Zwraca False dla pustych stringów, nieznanych symboli, nazw spółek
    (np. "BOGDANKA" zamiast "LWB").
    """
    if not ticker:
        return False
    cleaned = ticker.strip().upper()
    if cleaned.startswith("$"):
        cleaned = cleaned[1:]
    if not cleaned:
        return False
    return cleaned in get_gpw_tickers()


@lru_cache(maxsize=1)
def get_name_to_ticker_map() -> dict[str, str]:
    """
    Zwraca mapping nazwa spółki → kod GPW (UPPERCASE).

    Z `data/company_list.json` (entry: {ticker: "LWB.PL", nazwa: "BOGDANKA"}).
    Strip `.PL` z tickera. Klucz = uppercase nazwa.

    Używane w `xpost_validator._format_source_data` do pokazania Gemini
    aliasów (BOGDANKA == $LWB) — żeby nie uznawał kodów GPW za halucynację.

    Cache jak `get_gpw_tickers` (singleton w procesie).
    """
    if not _COMPANY_LIST_PATH.exists():
        return {}

    try:
        with open(_COMPANY_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    mapping: dict[str, str] = {}
    for entry in data.get("spolki", []):
        nazwa = (entry.get("nazwa") or "").strip().upper()
        ticker = (entry.get("ticker") or "").strip().upper()
        if not nazwa or not ticker:
            continue
        if ticker.endswith(".PL"):
            ticker = ticker[:-3]
        if ticker:
            mapping[nazwa] = ticker
    return mapping


def name_to_ticker(name: str) -> str | None:
    """
    Zwraca kod GPW dla nazwy spółki (case-insensitive). None jeśli brak.

    Np. name_to_ticker("BOGDANKA") → "LWB"
        name_to_ticker("CDPROJEKT") → "CDR"
        name_to_ticker("GRUPA AZOTY") → "ATT"  (normalizacja spacji)
        name_to_ticker("NEWAG S.A.") → "NWG"   (normalizacja sufiksu)
    """
    if not name:
        return None
    mapping = get_name_to_ticker_map()

    key = name.strip().upper()
    if key in mapping:
        return mapping[key]

    # Normalizacja: usuń spacje (espiebi: "GRUPA AZOTY" → "GRUPAAZOTY")
    no_spaces = key.replace(" ", "")
    if no_spaces in mapping:
        return mapping[no_spaces]

    # Normalizacja: usuń sufiks S.A./SA + spacje (espiebi: "NEWAG S.A." → "NEWAG")
    import re as _re
    stripped = _re.sub(r"[\s.]*S\.?A\.?[\s.]*$|[\s.]*SP\.?[\s.]*Z\.?[\s.]*O\.?[\s.]*O\.?[\s.]*$", "", key).strip()
    if stripped and stripped != key:
        if stripped in mapping:
            return mapping[stripped]
        no_spaces2 = stripped.replace(" ", "")
        if no_spaces2 in mapping:
            return mapping[no_spaces2]

    return None


# ── Display names (pełne nazwy firm dla xpostów) ─────────────────────────────

@lru_cache(maxsize=1)
def _load_display_names() -> dict[str, str]:
    """Ładuje ticker → pełna nazwa firmy z data/ticker_display_names.json."""
    if not _DISPLAY_NAMES_PATH.exists():
        return {}
    try:
        with open(_DISPLAY_NAMES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def ticker_to_display_name(ticker: str | None) -> str | None:
    """Zwraca pełną nazwę firmy dla tickera GPW (np. 'APR' → 'Auto Partner SA').

    Używane w required_lines xpost promptu: '$APR Auto Partner SA'.
    Źródło: data/ticker_display_names.json (generowany przez
    scripts/build_ticker_display_names.py + auto-update w slow-path scrapera).

    Returns None gdy brak wpisu (graceful fallback — post wychodzi z samym $TICKER).
    """
    if not ticker:
        return None
    key = ticker.strip().upper().lstrip("$")
    return _load_display_names().get(key)
