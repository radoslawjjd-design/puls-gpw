"""
Whitelist tickerów GPW i display names.

Źródło: data/company_list.json + data/ticker_display_names.json.
Wszystkie funkcje używają lru_cache — ładowanie pliku raz na proces.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_COMPANY_LIST_PATH   = Path(__file__).resolve().parent.parent / "data" / "company_list.json"
_DISPLAY_NAMES_PATH  = Path(__file__).resolve().parent.parent / "data" / "ticker_display_names.json"


@lru_cache(maxsize=1)
def get_gpw_tickers() -> frozenset[str]:
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
        if raw.endswith(".PL"):
            raw = raw[:-3]
        if raw:
            tickers.add(raw)
    return frozenset(tickers)


def is_known_gpw_ticker(ticker: str) -> bool:
    if not ticker:
        return False
    cleaned = ticker.strip().upper().lstrip("$")
    return bool(cleaned) and cleaned in get_gpw_tickers()


@lru_cache(maxsize=1)
def _load_display_names() -> dict[str, str]:
    if not _DISPLAY_NAMES_PATH.exists():
        return {}
    try:
        with open(_DISPLAY_NAMES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def ticker_to_display_name(ticker: str | None) -> str | None:
    if not ticker:
        return None
    return _load_display_names().get(ticker.strip().upper().lstrip("$"))
