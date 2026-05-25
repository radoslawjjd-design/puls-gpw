"""
Generator niedzielny "GPW Agenda — tydzień DD.MM–DD.MM" + fallback.

Extracted z agents/xpost_agent.py (Faza 4 krok 9/N).

UWAGA (redesign): okno weekly_agenda jest na liście KILL w F1 — funkcjonalność
przejmuje "Weekly Outlook" w F7 (broker_decisions scheduler nd 18:00 generuje
nowy thread, a niedzielny `sunday` 10:00 dostaje weekly outlook agenda+dywidend).
"""
from __future__ import annotations

import logging
from datetime import date

from agents.xpost.base import _SYSTEM, _call_gemini, _date_label
from agents.xpost.formatters import _extract_tweet, _strip_trailing_ticker_hashtags

logger = logging.getLogger(__name__)


def generate_xpost_weekly_agenda(
    agenda: dict,
    week_start: date,
    week_end: date,
) -> dict:
    """
    Generuje thread 'GPW Agenda — tydzień DD.MM–DD.MM'.
    Thread: tyle tweetów ile trzeba, ALL wydarzenia per dzień, nic nie pomijamy.
    """
    from agents.agenda_builder import format_agenda_for_prompt
    from config import XPOST_DISCLAIMER

    ws = week_start.strftime("%d.%m")
    we = week_end.strftime("%d.%m")
    agenda_text = format_agenda_for_prompt(agenda)

    prompt = (
        f"{_SYSTEM}\n\n"
        f"=== DANE: GPW AGENDA {_date_label(ws, we)} ===\n\n"
        f"{agenda_text}\n\n"
        f"=== ZADANIE ===\n"
        f"Wygeneruj WĄTEK 5-7 postów — Weekly Outlook tygodnia (każdy ≤ 280 znaków).\n"
        f"Wybierz TOP 5-7 najważniejszych pozycji tygodnia (priorytet: WIG20 > mWIG40 >\n"
        f"sWIG80 > inne; dywidendy z ex-date w ≤3 dni; going concern).\n\n"
        f"LIMITY (X 2026 — twarde):\n"
        f"- KAŻDY post ≤ 280 znaków.\n"
        f"- MAX 1 cashtag $TICKER per post.\n"
        f"- MAX 1 hashtag (zalecane #GPW w ostatnim).\n"
        f"- Brak linków URL.\n\n"
        f"STRUKTURA:\n"
        f"Post 1 — HOOK (bez cashtag/hashtag):\n"
        f"  Liczba wydarzeń tygodnia + 1 fakt. Przykład:\n"
        f"  \"Tydzień {_date_label(ws, we)}: 12 raportów, 5 dywidend, 3 WZA. 🧵\"\n\n"
        f"Posty 2..N-1 — 1 spółka per post (1 cashtag):\n"
        f"  📆 DD.MM (dzień): [emoji typu] $TICKER — zwięzły opis (1-2 zdania).\n"
        f"  Emoji per typ: 📊=wyniki, 🏛️=WZA, 💰=dywidendy, 🔔=rynek pierwotny/inne\n"
        f"  Przykład: \"📆 21.04 (pon): 💰 $LPP — dywidenda 400 zł/akcję, ustalenie 21.04.\"\n\n"
        f"Ostatni post — CLOSE:\n"
        f"  Pytanie zamykające + #GPW + stopka prawna.\n"
        f"  Stopka TYLKO TUTAJ (nie powtarzaj w innych postach):\n"
        f"  #GPW\n"
        f"  ⚖️ {XPOST_DISCLAIMER}. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.\n\n"
        f"REGUŁY:\n"
        f"- TOP 5-7 wydarzeń tygodnia — nie wymieniaj wszystkich (selekcja > kompletność)\n"
        f"- Tickery to KODY GPW (np. $LPP, $PKO, $LWB) — nie nazwy ($BOGDANKA = błąd)\n"
        f"- Tylko FAKTY z danych — zero interpretacji\n"
        f"- Skróć opis ale zachowaj rok (np. '2025' nie '25')\n\n"
        f"Odpowiedz WYŁĄCZNIE poprawnym JSON:\n"
        f"{{\"is_thread\": true, \"tweets\": [\"hook\", \"post 1 spółka\", ..., \"close + stopka\"]}}\n"
    )

    result = _call_gemini(prompt, metadata={
        "agent": "xpost", "window": "weekly_agenda",
        "date_from": str(week_start), "date_to": str(week_end),
    })

    if result and result.get("tweets"):
        tweets = [_extract_tweet(t) for t in result["tweets"] if _extract_tweet(t)]
        # Last tweet only: weekly agenda może być threadem, strip tylko ostatniego (ten ma disclaimer)
        if tweets:
            tweets[-1] = _strip_trailing_ticker_hashtags(tweets[-1])
            logger.info(f"xpost weekly_agenda Gemini OK: {len(tweets)} tweetów")
            is_thread = len(tweets) > 1
            return {"is_thread": is_thread, "tweets": tweets}

    # ── Fallback deterministyczny ──
    logger.warning("xpost weekly_agenda: Gemini fallback")
    return _build_weekly_agenda_fallback(agenda, ws, we)


def _build_weekly_agenda_fallback(agenda: dict, ws: str, we: str) -> dict:
    from collections import defaultdict
    from datetime import timedelta

    from config import XPOST_DISCLAIMER

    _POLISH_DAYS_SHORT = {
        0: "poniedziałek", 1: "wtorek", 2: "środa",
        3: "czwartek", 4: "piątek",
    }
    _T_EMOJI = {
        "Wyniki spółek": "📊", "WZA": "🏛️", "Dywidendy": "💰",
        "Rynek pierwotny": "🔔", "Debiuty": "🔔", "Splity": "✂️",
        "Wezwania": "📢", "Wycofania": "⏹️", "Zawieszenia": "⏸️",
        "Zmiany w indeksach": "🔄", "Dni wolne": "📅",
    }

    lines = [f"📅 GPW Agenda | {_date_label(ws, we)}", ""]

    by_date = defaultdict(list)
    for ev in agenda.get("events", []):
        by_date[ev["data"]].append(ev)

    # All 5 weekdays
    current = agenda["date_from"]
    while current <= agenda["date_to"]:
        d_str = current.strftime("%Y-%m-%d")
        d_short = current.strftime("%d.%m")
        day_name = _POLISH_DAYS_SHORT.get(current.weekday(), "")
        lines.append(f"📆 {d_short} ({day_name}):")

        day_events = by_date.get(d_str, [])
        if day_events:
            for ev in day_events:
                ticker = ev.get("ticker", "")
                typ = ev.get("typ", "")
                opis = (ev.get("opis") or "")[:100]
                emoji = _T_EMOJI.get(typ, "📋")
                if ticker:
                    lines.append(f"{emoji} #{ticker} — {opis}")
                else:
                    lines.append(f"{emoji} {opis}")
        else:
            lines.append("(brak wydarzeń)")
        lines.append("")
        current += timedelta(days=1)

    if agenda.get("dividends"):
        lines.append("💰 Dywidendy w tym tygodniu:")
        for div in agenda["dividends"]:
            ticker = div.get("ticker", "")
            kwota = div.get("dywidenda")
            stopa = div.get("stopa_proc")
            ustalenia = div.get("data_ustalenia", "?")
            k = f"{kwota:.2f}" if kwota else "?"
            s = f" ({stopa}%)" if stopa else ""
            lines.append(f"#{ticker} — {k} zł/akcję{s} | ustalenie {ustalenia}")
        lines.append("")

    lines.append("#GPW #giełda #FinTwit")
    lines.append("")
    lines.append(f"⚖️ {XPOST_DISCLAIMER}. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.")

    return {"is_thread": False, "tweets": ["\n".join(lines)]}
