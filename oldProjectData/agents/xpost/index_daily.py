"""
Generator wątku index_daily (podsumowanie D-1 per indeks GPW).

Extracted z agents/xpost_agent.py (Faza 4 krok 5/N).

UWAGA (redesign): w F6/F7 to okno zostanie WCHŁONIĘTE do daily_thread
(indeksy jako hook threadu). Patrz X_STRATEGY.md. Moduł zostaje dla
backward compat do czasu wyłączenia okna w prod + usunięcia w kolejnym
refactorze.
"""
from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime

from agents.xpost.base import _SYSTEM, _call_gemini
from agents.xpost.formatters import _extract_tweet
from agents.xpost.templates import _INDEX_DAILY_TEMPLATE

logger = logging.getLogger(__name__)

_INDEX_EMOJI = {"WIG20": "🔵", "mWIG40": "🟡", "sWIG80": "🟢", "Pozostałe": "📌"}
_INDEX_ORDER = ["WIG20", "mWIG40", "sWIG80", "Pozostałe"]
_INDEX_TWEET_MAX_CHARS = 3500


def _trim_index_tweet(tweet: str, max_chars: int = _INDEX_TWEET_MAX_CHARS) -> str:
    """Skraca tweet usuwając pozycje od końca (Pozostałe najpierw, potem ostatnie z sekcji).
    Zachowuje strukturę sekcji i hashtagi/disclaimer."""
    if len(tweet) <= max_chars:
        return tweet

    lines = tweet.split("\n")

    # Znajdź granicę: hashtagi + disclaimer na końcu (zachowaj je)
    footer_start = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("#") or stripped.startswith("⚖️") or stripped == "":
            footer_start = i
        else:
            break
    footer = lines[footer_start:]
    body = lines[:footer_start]

    # Usuwaj bullet pointy (•) od końca dopóki za długo
    while len("\n".join(body + footer)) > max_chars:
        # Szukaj ostatniego bullet pointa w body
        removed = False
        for i in range(len(body) - 1, -1, -1):
            if body[i].strip().startswith("•"):
                body.pop(i)
                # Usuń pustą sekcję — jeśli nagłówek (emoji) bez bullet pointów
                if i > 0 and i <= len(body):
                    # Sprawdź czy poprzednia linia to nagłówek sekcji bez elementów
                    remaining_after = body[i:] if i < len(body) else []
                    has_bullets_after = any(
                        l.strip().startswith("•") for l in remaining_after
                        if not any(l.strip().startswith(e) for e in ("🔵", "🟡", "🟢", "📌"))
                    )
                    if not has_bullets_after and i > 0:
                        prev = body[i - 1].strip()
                        if any(prev.startswith(e) for e in ("🔵", "🟡", "🟢", "📌")):
                            body.pop(i - 1)
                            # Usuń pustą linię przed nagłówkiem
                            if i - 2 >= 0 and body[i - 2].strip() == "":
                                body.pop(i - 2)
                removed = True
                break
        if not removed:
            break

    result = "\n".join(body + footer)
    logger.info(f"Trimmed tweet: {len(tweet)} → {len(result)} znaków")
    return result


def generate_xpost_index_daily(
    index_summaries: dict[str, list[dict]],
    data: date,
) -> dict:
    """
    Generuje wątek 2 tweetów z podsumowaniem dnia per indeks GPW.
    Tweet 1: WIG20 + mWIG40, Tweet 2: sWIG80 + opcjonalnie Pozostałe.
    index_summaries: {"WIG20": [{"ticker": "PKOBP", "summary": "...", "count": 2}, ...]}
    Zwraca: {"is_thread": True, "tweets": [str, str]}
    """
    today_str = datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
    system = _SYSTEM.format(today=today_str)

    # Build data block for Gemini context
    data_lines = []
    for idx in _INDEX_ORDER:
        items = index_summaries.get(idx, [])
        if not items:
            continue
        emoji = _INDEX_EMOJI.get(idx, "📌")
        data_lines.append(f"\n{emoji} {idx}:")
        for item in items:
            data_lines.append(f"  • #{item['ticker']} — {item['summary']}")
    index_data = "\n".join(data_lines)

    # Build pre-built structure per tweet
    # Tweet 1: WIG20 + mWIG40 | Tweet 2: sWIG80 + (Pozostałe only if budget allows)
    _TWEET1_INDICES = ["WIG20", "mWIG40"]

    # Estimate tweet 2 char budget — sWIG80 items * ~200 chars each
    swig_items = len(index_summaries.get("sWIG80", []))
    remaining_items = len(index_summaries.get("Pozostałe", []))
    # Each ticker takes ~180-250 chars. Budget: 3000 chars per tweet.
    # sWIG80 gets priority. Include Pozostałe only if sWIG80 < 4 items.
    include_remaining = swig_items <= 3 and remaining_items > 0
    _TWEET2_INDICES = ["sWIG80", "Pozostałe"] if include_remaining else ["sWIG80"]

    def _build_prebuilt(indices):
        lines = []
        for idx in indices:
            items = index_summaries.get(idx, [])
            if not items:
                continue
            emoji = _INDEX_EMOJI.get(idx, "📌")
            lines.append(f"{emoji} {idx}:")
            for item in items:
                lines.append(f"• #{item['ticker']} — [1-2 zwięzłe zdania z liczbami]")
            lines.append("")
        return "\n".join(lines)

    prebuilt_tweet1 = _build_prebuilt(_TWEET1_INDICES)
    prebuilt_tweet2 = _build_prebuilt(_TWEET2_INDICES)

    prompt = f"{system}\n\n" + _INDEX_DAILY_TEMPLATE.format(
        data=data.strftime("%d.%m.%Y"),
        data_short=data.strftime("%d.%m"),
        index_data=index_data,
        prebuilt_tweet1=prebuilt_tweet1,
        prebuilt_tweet2=prebuilt_tweet2,
    )

    result = _call_gemini(prompt, metadata={
        "agent":  "xpost",
        "window": "index_daily",
        "date":   str(data),
        "wig20_count":  len(index_summaries.get("WIG20", [])),
        "mwig40_count": len(index_summaries.get("mWIG40", [])),
        "swig80_count": swig_items,
    })

    if result and "tweets" in result:
        result["tweets"] = [_extract_tweet(t) for t in result["tweets"]]
        # Post-processing: trim tweets to max chars (usuwanie pozycji od końca)
        result["tweets"] = [_trim_index_tweet(t) for t in result["tweets"]]
        result["is_thread"] = True
        logger.info(
            f"xpost index_daily wygenerowany: {len(result['tweets'])} tweetów, "
            f"znaki: {[len(t) for t in result['tweets']]}"
        )
        return result

    # Fallback — surowe dane bez Gemini, 2 tweety
    logger.warning("Gemini fallback dla index_daily")

    def _fallback_tweet(indices, header=""):
        lines = [header] if header else []
        for idx in indices:
            items = index_summaries.get(idx, [])
            if not items:
                continue
            emoji = _INDEX_EMOJI.get(idx, "📌")
            lines.append(f"\n{emoji} {idx}:")
            for item in items[:4]:
                lines.append(f"• #{item['ticker']} — {item['summary'][:80]}")
        return "\n".join(lines)

    tweet1 = _fallback_tweet(_TWEET1_INDICES, f"📊 GPW Indeksy | {data.strftime('%d.%m')} (1/2) - #WIG20 #mWIG40 #sWIG80")
    tweet2 = _fallback_tweet(_TWEET2_INDICES) + (
        "\n\n#GPW #ESPI #giełda #FinTwit\n\n"
        "⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. "
        "Inwestujesz na własne ryzyko."
    )
    return {"is_thread": True, "tweets": [tweet1, tweet2]}
