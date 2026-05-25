"""
Generator niedzielnego posta "Makro & kontekst rynkowy".

Extracted z agents/xpost_agent.py (Faza 4 krok 3/N).

UWAGA (F7): w redesignie niedzielne okno zmienia focus — zamiast "Makro"
będzie "Decyzje brokerów". Ten generator zostaje tymczasowo, aż F7 dostarczy
alternatywę. Po F7 można usunąć / oznaczyć deprecated.
"""
from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime

from agents.xpost.base import _SYSTEM, _call_gemini
from agents.xpost.formatters import _extract_tweet, _fmt_index
from agents.xpost.templates import _SUNDAY_TEMPLATE

logger = logging.getLogger(__name__)


def _format_agenda_for_sunday_prompt(weekly_agenda: dict) -> str:
    """Formatuje weekly_agenda do tekstu dla promptu sunday X-post.

    Output: 5 dni (pn-pt) z grupowaniem per typ + listą spółek (plain text).
    """
    if not weekly_agenda:
        return ""
    from collections import defaultdict
    from datetime import timedelta

    _DAY_NAMES = {0: "PONIEDZIAŁEK", 1: "WTOREK", 2: "ŚRODA",
                  3: "CZWARTEK", 4: "PIĄTEK"}
    _T_EMOJI = {"Wyniki spółek": "📊", "WZA": "🏛️",
                "Dywidendy": "💰", "Rynek pierwotny": "🔔"}

    by_day: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for ev in weekly_agenda.get("events") or []:
        by_day[ev.get("data", "")][ev.get("typ", "Inne")].append(ev)

    parts = []
    df = weekly_agenda.get("date_from")
    dt = weekly_agenda.get("date_to")
    if df and dt:
        parts.append(f"=== AGENDA TYGODNIA {df.strftime('%d.%m')}-{dt.strftime('%d.%m.%Y')} ===")
        parts.append(
            f"Łącznie: {weekly_agenda.get('event_count', 0)} wydarzeń, "
            f"{weekly_agenda.get('dividend_count', 0)} dywidend (ex-date w tygodniu).\n"
        )

    # Iteruj 5 dni
    if df:
        current = df
        while current <= dt:
            d_str = current.strftime("%Y-%m-%d")
            day_label = _DAY_NAMES.get(current.weekday(), "")
            day_short = current.strftime("%d.%m")
            day_events: dict[str, list[dict]] = by_day.get(d_str, {})
            n_total = sum(len(items) for items in day_events.values())
            parts.append(f"📆 {day_label} {day_short} ({n_total} wydarzeń)")
            if not day_events:
                parts.append("  (brak wydarzeń)")
            else:
                for typ in sorted(day_events.keys()):
                    items = day_events[typ]
                    emoji = _T_EMOJI.get(typ, "•")
                    parts.append(f"  {emoji} {typ} ({len(items)}):")
                    for ev in items[:30]:
                        ticker = ev.get("ticker", "?")
                        opis = (ev.get("opis") or "")[:120]
                        parts.append(f"    {ticker} — {opis}")
                    if len(items) > 30:
                        parts.append(f"    ... i {len(items) - 30} więcej")
            parts.append("")
            current += timedelta(days=1)

    # Top dywidendy
    divs = weekly_agenda.get("dividends") or []
    if divs:
        sorted_divs = sorted(divs, key=lambda d: d.get("stopa_proc") or 0.0, reverse=True)
        parts.append("=== TOP DYWIDENDY (yield-sorted) ===")
        for d in sorted_divs[:8]:
            parts.append(
                f"  {d.get('ticker', '?')} — {d.get('dywidenda', 0):.2f} zł "
                f"({d.get('stopa_proc', 0):.2f}%), ex-date {d.get('data_ustalenia', '?')}"
            )
        parts.append("")

    # Strategy A — mapping nazwa→kod GPW dla cashtagów
    # (bez tego Gemini halucynuje $INGBSK zamiast $ING, $BNPPPL zamiast $BNP)
    from utils.gpw_tickers import get_gpw_tickers, name_to_ticker
    gpw_set = get_gpw_tickers()
    seen: set[str] = set()
    mapping_lines = []

    def _strip_suffix(name: str) -> str:
        """OPONEO.PL → OPONEO; SILVAIR-REGS bez zmian."""
        for suf in (".PL", ".COM", ".SA", ".EU"):
            if name.endswith(suf):
                name = name[: -len(suf)]
        return name

    for src in (weekly_agenda.get("events") or []) + (weekly_agenda.get("dividends") or []):
        spolka_raw = (src.get("ticker") or "").strip().upper()
        spolka = _strip_suffix(spolka_raw)
        if not spolka or spolka in seen:
            continue
        seen.add(spolka)
        kod = name_to_ticker(spolka)
        if kod:
            mapping_lines.append(f"  - {spolka} → ${kod}")
        elif spolka in gpw_set:
            mapping_lines.append(f"  - {spolka} → ${spolka} (nazwa == kod)")
        else:
            # Spoza whitelist: użyj nazwy jako fallback (NewConnect spółki)
            mapping_lines.append(f"  - {spolka} → ${spolka} (brak w whitelist GPW)")

    if mapping_lines:
        parts.append("=== WYMAGANE KODY GPW (UŻYJ DOKŁADNIE TYCH CASHTAGÓW) ===")
        parts.append("Każde użycie **$TICKER** w long postach MUSI być z TEJ LISTY.")
        parts.append("NIE używaj nazw spółek jako cashtag (np. $BNPPPL = BŁĄD → użyj $BNP).")
        parts.extend(mapping_lines[:50])  # cap 50 żeby prompt nie urósł nadmiernie
        if len(mapping_lines) > 50:
            parts.append(f"  ... i {len(mapping_lines) - 50} więcej spółek")
        parts.append("")

    return "\n".join(parts)


def generate_xpost_sunday(
    macro_data: dict,
    data: date,
    weekly_agenda: dict | None = None,
) -> dict:
    """
    Generuje niedzielny X-post "Weekly Outlook" — agenda + dywidendy + makro.

    Po fix 2026-04-19 (X Premium long-form):
    - Thread 7 long postów (1 hook + 5 dni + 1 close)
    - Każdy long post (~1500-4000 zn) = pełna agenda 1 dnia
    - Plain text spółek + markdown bold dla cashtagów

    Args:
        macro_data: dane makro z _load_macro (indeksy/waluty/surowce/NBP)
        data: data referencyjna posta
        weekly_agenda: opcjonalnie agenda z build_weekly_agenda()
                       (gdy None — fallback do starego makro-only formatu)
    """
    today_str = datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
    system = _SYSTEM.format(today=today_str)

    indeksy = macro_data.get("indeksy", {})
    surowce = macro_data.get("surowce", {})
    waluty = macro_data.get("waluty", {})
    makro_pl = macro_data.get("makro_pl", {})
    stopy = makro_pl.get("stopy_procentowe", {})
    inflacja_data = makro_pl.get("inflacja", {})

    indeksy_str = "\n".join(
        _fmt_index(indeksy, name)
        for name in ["WIG20", "WIG", "MWIG40", "SWIG80", "DAX", "SP500", "NASDAQ"]
        if name in indeksy
    ) or "brak danych"

    surowce_str = "\n".join(
        _fmt_index(surowce, name)
        for name in ["ROPA_BRENT", "ZLOTO", "MIEDZ", "GAZ_NYMEX"]
        if name in surowce
    ) or "brak danych"

    waluty_str = "\n".join(
        _fmt_index(waluty, name)
        for name in ["USDPLN", "EURPLN", "EURUSD", "USDPLN_NBP", "EURPLN_NBP",
                      "GBPPLN_NBP", "CHFPLN_NBP"]
        if name in waluty
    ) or "brak danych"

    data_makro = macro_data.get("data", data.strftime("%Y-%m-%d"))

    # "2026-04-09" → "09.04.2026"
    try:
        _d = date.fromisoformat(data_makro)
        data_makro_pl = _d.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        data_makro_pl = data_makro

    # F-2026-04-19: dla X Premium long-form — wstrzyknij agendę przed makro.
    agenda_block = _format_agenda_for_sunday_prompt(weekly_agenda) if weekly_agenda else ""

    prompt = f"{system}\n\n" + _SUNDAY_TEMPLATE.format(
        data_makro=data_makro,
        data_makro_pl=data_makro_pl,
        indeksy=indeksy_str,
        surowce=surowce_str,
        waluty=waluty_str,
        stopa_ref=stopy.get("stopa_referencyjna_nbp", "?"),
        inflacja=inflacja_data.get("inflacja_cpi_rdr", "?"),
        inflacja_okres=inflacja_data.get("okres", "?"),
        agenda_block=agenda_block,
    )

    # ── Cashtag v2 opt-in: append override suffix z nowymi regułami ─────────
    from config import XPOST_CASHTAG_V2_WINDOWS
    if "sunday" in XPOST_CASHTAG_V2_WINDOWS:
        from agents.xpost.cashtag_rules import CASHTAG_V2_OVERRIDE_SUFFIX
        prompt += CASHTAG_V2_OVERRIDE_SUFFIX

    result = _call_gemini(prompt, metadata={
        "agent":      "xpost",
        "window":     "sunday",
        "date":       str(data),
        "data_makro": data_makro,
    })

    if result and "tweets" in result:
        result["tweets"] = [_extract_tweet(t) for t in result["tweets"]]
        result["is_thread"] = False
        logger.info(f"xpost sunday wygenerowany: {len(result['tweets'][0])} znaków")
        return result

    logger.warning("Gemini fallback dla sunday")
    wig20 = indeksy.get("WIG20", {})
    fallback = (
        f"📈 Makro & rynki | {data_makro}\n"
        f"WIG20: {wig20.get('cena', '?')} ({'+' if wig20.get('zmiana_proc', 0) >= 0 else ''}"
        f"{wig20.get('zmiana_proc', '?')}%)\n"
        f"Stopa ref. NBP: {stopy.get('stopa_referencyjna_nbp', '?')}% | "
        f"Inflacja CPI: {inflacja_data.get('inflacja_cpi_rdr', '?')}% r/r\n"
        f"#GPW #makro #giełda #FinTwit\n\n"
        f"⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko."
    )
    return {"is_thread": False, "tweets": [fallback]}
