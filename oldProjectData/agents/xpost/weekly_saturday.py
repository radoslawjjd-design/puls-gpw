"""
Generator sobotniego posta "Tydzień na GPW w liczbach".

Extracted z agents/xpost_agent.py (Faza 4 krok 4/N).

W F6 ten generator zostanie rozszerzony żeby wchłaniał cytaty tygodnia
(zamiast osobnego daily quotes window) — patrz X_STRATEGY.md.
"""
from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime

from agents.xpost.base import _SYSTEM, _call_gemini
from agents.xpost.formatters import (
    _extract_tweet,
    _fmt_list,
    _fmt_top,
    _merge_top_announcements,
)
from agents.xpost.templates import _SATURDAY_TEMPLATE

logger = logging.getLogger(__name__)


def generate_xpost_saturday(
    weekly_summary: dict,
    data: date,
) -> dict:
    """
    Generuje sobotni post "Tydzień na GPW w liczbach" z weekly summary.
    Zwraca: {"is_thread": False, "tweets": [str]}
    """
    today_str = datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
    system = _SYSTEM.format(today=today_str)

    # BQ schema: date_from/date_to (EN-snake). Backward-compat: data_od/data_do (PL).
    # Konwersja date object → ISO string ("2026-04-13").
    def _to_iso(v):
        if v is None or v == "":
            return "?"
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    data_od = _to_iso(weekly_summary.get("date_from") or weekly_summary.get("data_od"))
    data_do = _to_iso(weekly_summary.get("date_to") or weekly_summary.get("data_do"))

    # Merge top announcements into flat list
    top_poz = (weekly_summary.get("top_pozytywne") or [])[:5]
    top_neg = (weekly_summary.get("top_negatywne") or [])[:5]
    merged = _merge_top_announcements(top_poz, top_neg, 10)

    sektory_str = "\n".join(
        f"• {s.get('sektor', '?')}: {s.get('liczba_ogloszen', 0)} ogł."
        for s in (weekly_summary.get("sektory_aktywne") or [])[:6]
    ) or "brak danych"

    def _to_dd_mm(iso_str):
        """ISO 'YYYY-MM-DD' → 'DD.MM' (Polski format dat)."""
        if iso_str == "?" or len(iso_str) < 10:
            return iso_str
        try:
            return date.fromisoformat(iso_str[:10]).strftime("%d.%m")
        except (ValueError, TypeError):
            return iso_str

    data_od_short = _to_dd_mm(data_od)
    data_do_short = _to_dd_mm(data_do)

    # Adaptive: ile spółek pokazać w threadzie. Saturday flagowiec — preferujemy
    # 5 spółek (target_n=7 = hook+5+close) bo to weekly podsumowanie.
    # Fallback do mniej gdy mało danych.
    n_companies = min(5, len(merged))
    target_n = n_companies + 2  # hook + N + close
    top_news = merged[:n_companies]

    # F6.6 Strategy A: prekomputuj WYMAGANE KODY GPW dla każdej spółki w top_news.
    # Bez tego Gemini halucynuje (zaobserwowano 2026-04-18: $TES, $CPL — nieist.).
    from utils.gpw_tickers import get_gpw_tickers, name_to_ticker
    gpw_set = get_gpw_tickers()
    required_lines = []
    for news in top_news:
        spolka = (news.get("spolka") or "").strip().upper()
        if not spolka:
            continue
        kod = name_to_ticker(spolka)
        if kod:
            required_lines.append(f"  - {spolka} → ${kod}")
        elif spolka in gpw_set:
            required_lines.append(f"  - {spolka} → ${spolka} (nazwa == kod)")
        else:
            required_lines.append(f"  - {spolka} → ${spolka} (brak w whitelist)")
    required_block = (
        "\n=== WYMAGANE KODY GPW DLA TYCH SPÓŁEK (UŻYJ DOKŁADNIE TYCH CASHTAGÓW) ===\n"
        + "\n".join(required_lines)
        + "\nKAŻDY post o spółce MUSI mieć cashtag z TEJ LISTY (nie pomijaj $!).\n"
    )

    # ── Cashtag v2: use _THREAD_TEMPLATE_V2 (jak intraday) dla bullet structure ─
    # Stary _SATURDAY_TEMPLATE generuje 1-zdaniówki per spółka (135-160 zn) bez
    # bullet listy. _THREAD_TEMPLATE_V2 ma explicit bullet examples i wymusza
    # strukturę "🩺 $TICKER — nagłówek:\n• punkt 1\n• punkt 2\n..."
    from config import XPOST_CASHTAG_V2_WINDOWS

    if "saturday" in XPOST_CASHTAG_V2_WINDOWS:
        from agents.xpost import templates as _tpl
        from agents.xpost.cashtag_rules import CASHTAG_INSTRUCTIONS_PROMPT

        body_template = _tpl._THREAD_TEMPLATE_V2.format(
            okno = f"{data_od_short}–{data_do_short} (tydzień)",
            data = f"{data_od_short}–{data_do_short}",
            data_short = f"{data_od_short}–{data_do_short}",
            top_ogłoszenia = _fmt_top(top_news, n_companies),
            sektory = sektory_str,
            trendy = _fmt_list(weekly_summary.get("trendy_i_wzorce", []), 4),
            ryzyka = _fmt_list(weekly_summary.get("ryzyka_rynkowe", []), 3),
            szanse = _fmt_list(weekly_summary.get("szanse_rynkowe", []), 3),
            cashtag_instructions = CASHTAG_INSTRUCTIONS_PROMPT,
            suggestions_context = "",
        )
        prompt = (
            f"{system}\n\n"
            + body_template
            + required_block
            + (
                f"\n=== DOCELOWA LICZBA POSTÓW W WĄTKU: {target_n} ===\n"
                f"Wygeneruj DOKŁADNIE {target_n} postów: "
                f"1 hook + {n_companies} spółek + 1 close.\n"
            )
        )
    else:
        prompt = (
            f"{system}\n\n"
            + _SATURDAY_TEMPLATE.format(
                data_od=data_od,
                data_do=data_do,
                data_od_short=data_od_short,
                data_do_short=data_do_short,
                top_ogłoszenia=_fmt_top(top_news, n_companies),
                sektory=sektory_str,
                trendy=_fmt_list(weekly_summary.get("trendy_i_wzorce", []), 4),
                ryzyka=_fmt_list(weekly_summary.get("ryzyka_rynkowe", []), 3),
                szanse=_fmt_list(weekly_summary.get("szanse_rynkowe", []), 3),
            )
            + required_block
            + (
                f"\n=== DOCELOWA LICZBA POSTÓW W WĄTKU: {target_n} ===\n"
                f"Wygeneruj DOKŁADNIE {target_n} postów: "
                f"1 hook + {n_companies} spółek + 1 close.\n"
                f"WAŻNE: w hook podaj liczbę {n_companies} spółek (nie więcej!), "
                f"żeby było spójne z liczbą postów w wątku.\n"
            )
        )

    result = _call_gemini(prompt, metadata={
        "agent":  "xpost",
        "window": "saturday",
        "date":   str(data),
        "data_od": data_od,
        "data_do": data_do,
    })

    if result and "tweets" in result:
        result["tweets"] = [_extract_tweet(t) for t in result["tweets"]]
        # FIX 2026-04-28: detect thread by liczba postów (cashtag-v2 generuje 5-7
        # tweetów, stara logika hardcodowała is_thread=False powodując fail
        # validatora — single CHAR_LIMITS 280 zn vs body 297 zn z cashtag-v2).
        result["is_thread"] = len(result["tweets"]) > 1
        logger.info(
            f"xpost saturday wygenerowany: {len(result['tweets'])} tweetów, "
            f"is_thread={result['is_thread']}"
        )
        return result

    logger.warning("Gemini fallback dla saturday")
    fallback = (
        f"📊 Tydzień na GPW | {data_od_short}–{data_do_short}\n"
        f"Podsumowanie ogłoszeń ESPI/EBI.\n"
        f"#GPW\n"
        f"⚖️ Nie stanowi rekomendacji inwestycyjnej."
    )
    return {"is_thread": False, "tweets": [fallback]}
