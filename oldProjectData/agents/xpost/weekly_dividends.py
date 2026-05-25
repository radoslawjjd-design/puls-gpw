"""
Generator sobotni "Dywidendy GPW — podsumowanie tygodnia" + fallback.

Extracted z agents/xpost_agent.py (Faza 4 krok 8/N).

UWAGA (redesign): okno weekly_dividends jest na liście KILL w F1 — wchłania
się do niedzielnego Weekly Outlook (agenda+dywidendy w 1 threadzie).
"""
from __future__ import annotations

import logging
from datetime import date

from agents.xpost.base import _SYSTEM, _call_gemini
from agents.xpost.formatters import _extract_tweet

logger = logging.getLogger(__name__)


def generate_xpost_weekly_dividends(
    dividends: list[dict],
    week_start: date,
    week_end: date,
) -> dict:
    """
    Generuje thread 'Dywidendy GPW — podsumowanie tygodnia'.
    Thread: tyle tweetów ile trzeba, ALL dywidendy, nic nie pomijamy.
    """
    from config import XPOST_DISCLAIMER

    ws = week_start.strftime("%d.%m")
    we = week_end.strftime("%d.%m")

    prompt = (
        f"{_SYSTEM}\n\n"
        f"=== DANE: DYWIDENDY GPW — PODSUMOWANIE TYGODNIA {ws}–{we} ===\n\n"
    )
    for d in dividends:
        ticker = d.get("ticker", "")
        kwota = d.get("dywidenda")
        stopa = d.get("stopa_proc")
        ustalenia = d.get("data_ustalenia", "?")
        wyplaty = d.get("data_wyplaty", "?")
        analiza = d.get("analiza") or {}
        zmiana = analiza.get("zmiana_rr")
        poprz = analiza.get("poprzednia")
        prompt += f"  • {ticker}: {kwota:.2f} zł/akcję"
        if stopa:
            prompt += f" (stopa {stopa}%)"
        prompt += f", ustalenie: {ustalenia}, wypłata: {wyplaty}"
        if zmiana is not None:
            sign = "+" if zmiana > 0 else ""
            prompt += f", kwota {sign}{zmiana}% r/r"
        if poprz:
            prompt += f", poprzednia: {poprz:.2f} zł"
        prompt += "\n"

    n_divs = len(dividends)
    prompt += (
        f"\n=== ZADANIE ===\n"
        f"Wygeneruj WĄTEK 3-{max(3, min(n_divs + 2, 8))} postów (każdy ≤ 280 znaków).\n"
        f"UWZGLĘDNIJ WSZYSTKIE {n_divs} dywidend(y) (priorytet: ustalenie ≤3 dni; rekord r/r).\n\n"
        f"LIMITY (X 2026 — twarde):\n"
        f"- KAŻDY post ≤ 280 znaków.\n"
        f"- MAX 1 cashtag $TICKER per post.\n"
        f"- MAX 1 hashtag (zalecane #dywidendy w ostatnim).\n"
        f"- Brak linków URL.\n\n"
        f"STRUKTURA:\n"
        f"Post 1 — HOOK (bez cashtag/hashtag):\n"
        f"  Liczba dywidend + 1 najmocniejszy fakt. Przykład:\n"
        f"  \"{n_divs} dywidend(y) GPW {ws}–{we}: top stopa X.XX%. 🧵\"\n\n"
        f"Posty 2..N-1 — 1 dywidenda per post (1 cashtag):\n"
        f"  💰 $TICKER — kwota zł/akcję (stopa%)\n"
        f"  📆 ustalenie DD.MM | wypłata DD.MM\n"
        f"  Opcjonalnie: kwota +X% r/r (jeśli dane z poprz. roku).\n\n"
        f"Ostatni post — CLOSE:\n"
        f"  Pytanie zamykające + #dywidendy + stopka prawna.\n"
        f"  Stopka TYLKO TUTAJ (nie powtarzaj w innych postach):\n"
        f"  #dywidendy\n"
        f"  ⚖️ {XPOST_DISCLAIMER}. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.\n\n"
        f"REGUŁY:\n"
        f"- WSZYSTKIE pozycje z danych — nie pomijaj żadnej dywidendy\n"
        f"- Tickery to KODY GPW (np. $LPP, $PKO) — nie nazwy\n"
        f"- Użyj WYŁĄCZNIE danych podanych wyżej — zero wymyślonych wartości\n"
        f"- Tylko FAKTY — zero interpretacji\n\n"
        f"Odpowiedz WYŁĄCZNIE poprawnym JSON:\n"
        f"{{\"is_thread\": true, \"tweets\": [\"hook\", \"post 1 div\", ..., \"close + stopka\"]}}\n"
    )

    result = _call_gemini(prompt, metadata={
        "agent": "xpost", "window": "weekly_dividends",
        "date_from": str(week_start), "date_to": str(week_end),
    })

    if result and result.get("tweets"):
        tweets = [_extract_tweet(t) for t in result["tweets"] if _extract_tweet(t)]
        if tweets:
            logger.info(f"xpost weekly_dividends Gemini OK: {len(tweets)} tweetów")
            is_thread = len(tweets) > 1
            return {"is_thread": is_thread, "tweets": tweets}

    # ── Fallback deterministyczny ──
    logger.warning("xpost weekly_dividends: Gemini fallback")
    return _build_weekly_dividends_fallback(dividends, ws, we)


def _build_weekly_dividends_fallback(dividends: list[dict], ws: str, we: str) -> dict:
    from config import XPOST_DISCLAIMER
    lines = [f"💰 Dywidendy GPW | podsumowanie tygodnia {ws}–{we}", ""]

    for d in dividends:
        ticker = d.get("ticker", "")
        kwota = d.get("dywidenda")
        stopa = d.get("stopa_proc")
        ustalenia = d.get("data_ustalenia", "?")
        wyplaty = d.get("data_wyplaty", "?")
        analiza = d.get("analiza") or {}

        k = f"{kwota:.2f}" if kwota else "?"
        s = f" ({stopa}%)" if stopa else ""
        lines.append(f"#{ticker} — {k} zł/akcję{s}")
        lines.append(f"📆 ustalenie {ustalenia} | wypłata {wyplaty}")

        zmiana = analiza.get("zmiana_rr")
        poprz = analiza.get("poprzednia")
        parts = []
        if analiza.get("rekord"):
            parts.append("🏆 Rekord!")
        if zmiana is not None:
            sign = "+" if zmiana > 0 else ""
            parts.append(f"kwota {sign}{zmiana}% r/r")
        if poprz:
            parts.append(f"poprz. {poprz:.2f} zł")
        if parts:
            lines.append(" | ".join(parts))
        lines.append("")

    tickers = [d["ticker"] for d in dividends if d.get("ticker")]
    hashtags = " ".join(f"#{t}" for t in tickers[:10])
    lines.append(f"#GPW #giełda #dywidendy #FinTwit {hashtags}")
    lines.append("")
    lines.append(f"⚖️ {XPOST_DISCLAIMER}. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.")

    return {"is_thread": False, "tweets": ["\n".join(lines)]}
