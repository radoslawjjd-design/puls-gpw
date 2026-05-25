"""
Generator posta GPW Agenda — kalendarz korporacyjny + dywidendy.

Extracted z agents/xpost_agent.py (Faza 4 krok 7/N).

UWAGA (redesign): okno agenda daily jest na liście KILL w F1. W F6 agenda
zostanie wchłonięta do niedzielnego Weekly Outlook (cały tydzień jednym threadem).
"""
from __future__ import annotations

import logging
from datetime import date

from agents.xpost.base import _SYSTEM, _call_gemini, _date_label
from agents.xpost.formatters import _extract_tweet, _strip_trailing_ticker_hashtags

logger = logging.getLogger(__name__)


def generate_xpost_agenda(
    agenda: dict,
    data: date,
) -> dict:
    """
    Generuje post 'GPW Agenda' — kalendarz korporacyjny + dywidendy.
    Single post (nie wątek). Gemini formatuje, fallback deterministyczny.
    Zwraca: {"is_thread": False, "tweets": [str]}
    """
    from agents.agenda_builder import format_agenda_for_prompt
    from config import XPOST_DISCLAIMER

    data_short = data.strftime("%d.%m")
    date_from = agenda["date_from"]
    date_to = agenda["date_to"]
    df_short = date_from.strftime("%d.%m")
    dt_short = date_to.strftime("%d.%m")

    agenda_text = format_agenda_for_prompt(agenda)

    prompt = (
        f"{_SYSTEM}\n\n"
        f"=== DANE: GPW AGENDA {_date_label(df_short, dt_short)} ===\n\n"
        f"{agenda_text}\n\n"
        f"=== ZADANIE ===\n"
        f"Wygeneruj WĄTEK 3-5 postów (każdy ≤ 280 znaków).\n"
        f"Wybierz TOP 3-5 najważniejszych pozycji (mWIG40+/dywidendy w 3 dni/going concern).\n\n"
        f"LIMITY (X 2026 — twarde):\n"
        f"- KAŻDY post ≤ 280 znaków.\n"
        f"- MAX 1 cashtag $TICKER per post.\n"
        f"- MAX 1 hashtag (zalecane #GPW w ostatnim).\n"
        f"- Brak linków URL.\n\n"
        f"STRUKTURA:\n"
        f"Post 1 — HOOK (bez cashtag/hashtag):\n"
        f"  Liczba wydarzeń + 1 fakt. Np.: \"5 najważniejszych wydarzeń z agendy {_date_label(df_short, dt_short)}. 🧵\"\n\n"
        f"Posty 2..N-1 — 1 spółka per post (1 cashtag):\n"
        f"  [emoji typu] $TICKER — zwięzły opis (1-2 zdania).\n"
        f"  Emoji per typ: 📊 = wyniki/raporty, 🏛️ = WZA, 💰 = dywidendy, 🔔 = rynek pierwotny/inne\n"
        f"  Przykład: \"📊 $AMC — raport roczny skonsolidowany za 2025.\"\n"
        f"  Przykład: \"💰 $LPP — dywidenda 400 zł/akcję (1.77%), ustalenie 21.04.\"\n\n"
        f"Ostatni post — CLOSE:\n"
        f"  Pytanie zamykające + #GPW + stopka prawna.\n"
        f"  Stopka TYLKO TUTAJ (nie powtarzaj w innych postach):\n"
        f"  #GPW\n"
        f"  ⚖️ {XPOST_DISCLAIMER}. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.\n\n"
        f"REGUŁY:\n"
        f"- TOP wydarzenia (priorytet: WIG20/mWIG40 > sWIG80 > inne) — nie wymieniaj wszystkich\n"
        f"- Tickery to KODY GPW (np. $LPP, $PKO, $LWB) — nie nazwy ($BOGDANKA = błąd)\n"
        f"- Tylko FAKTY z danych — zero interpretacji\n"
        f"- Skróć opis ale zachowaj rok (np. '2025' nie '25')\n\n"
        f"Odpowiedz WYŁĄCZNIE poprawnym JSON:\n"
        f"{{\"is_thread\": true, \"tweets\": [\"hook\", \"post 1 spółka\", ..., \"close + stopka\"]}}\n"
    )

    result = _call_gemini(prompt, metadata={
        "agent":     "xpost",
        "window":    "agenda",
        "date":      str(data),
        "date_from": str(date_from),
        "date_to":   str(date_to),
    })

    if result and result.get("tweets"):
        tweets = [_extract_tweet(t) for t in result["tweets"] if _extract_tweet(t)]
        if tweets:
            # Stopka prawna tylko w ostatnim — strip tickerowych hashtagów wyłącznie tam.
            tweets[-1] = _strip_trailing_ticker_hashtags(tweets[-1])
            is_thread = len(tweets) > 1
            logger.info(
                f"xpost agenda Gemini OK: {len(tweets)} post(ów), "
                f"znaki: {[len(t) for t in tweets]}"
            )
            return {"is_thread": is_thread, "tweets": tweets}

    # ── Fallback deterministyczny ──
    logger.warning("xpost agenda: Gemini fallback — budowanie deterministyczne")
    return _build_agenda_fallback(agenda, data_short, df_short, dt_short)


def _build_agenda_fallback(agenda: dict, data_short: str, df_short: str, dt_short: str) -> dict:
    """Deterministyczny fallback dla posta agenda (bez Gemini)."""
    from config import XPOST_DISCLAIMER

    lines = [f"📅 GPW Agenda | {_date_label(df_short, dt_short)}", ""]

    _POLISH_DAYS_SHORT = {
        0: "poniedziałek", 1: "wtorek", 2: "środa",
        3: "czwartek", 4: "piątek", 5: "sobota", 6: "niedziela",
    }
    _TYPE_EMOJI = {
        "Wyniki spółek": "📊", "WZA": "🏛️", "Dywidendy": "💰",
        "Rynek pierwotny": "🔔", "Debiuty": "🔔", "Splity": "✂️",
        "Wezwania": "📢", "Wycofania": "⏹️", "Zawieszenia": "⏸️",
        "Zmiany w indeksach": "🔄", "Dni wolne": "📅",
    }

    if agenda["events"]:
        from collections import defaultdict
        by_date = defaultdict(list)
        for ev in agenda["events"]:
            by_date[ev["data"]].append(ev)

        for d in sorted(by_date.keys()):
            try:
                from datetime import date as _date
                dt = _date.fromisoformat(d)
                day_label = f"📆 {dt.strftime('%d.%m')} ({_POLISH_DAYS_SHORT.get(dt.weekday(), '')})"
            except ValueError:
                day_label = f"📆 {d}"
            lines.append(day_label)
            for ev in by_date[d][:8]:
                ticker = ev.get("ticker", "")
                typ = ev.get("typ", "")
                opis = (ev.get("opis") or "")[:100]
                emoji = _TYPE_EMOJI.get(typ, "📋")
                if ticker:
                    lines.append(f"{emoji} #{ticker} — {opis}")
                else:
                    lines.append(f"{emoji} {opis}")
            lines.append("")

    if agenda["dividends"]:
        lines.append("💰 Dywidendy:")
        for div in agenda["dividends"][:5]:
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
