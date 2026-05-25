"""
Agenda builder — assembly danych kalendarza korporacyjnego + nadchodzących dywidend.

Łączy dane z BQ (calendar_events + dividends) w struktury gotowe
do generowania postów X i emaili.

Tryby:
  - build_agenda()              — codzienny reminder: jutro (1 dzień roboczy) + dywidendy do pt
  - build_weekly_agenda()       — niedzielny przegląd: pon-pt nadchodzącego tygodnia
  - build_past_week_dividends() — sobotnia retrospekcja: dywidendy z minionego pon-pt
"""
import logging
from collections import defaultdict
from datetime import date, timedelta

from storage.bq_client import get_bq_client

logger = logging.getLogger(__name__)

_POLISH_DAYS = {
    0: "poniedziałek", 1: "wtorek", 2: "środa",
    3: "czwartek", 4: "piątek", 5: "sobota", 6: "niedziela",
}

_DIVIDEND_WINDOW_DAYS = 7


# ── Helpers ────────────────────────────────────────────────────────────────────

def _next_trading_day(from_date: date, n: int) -> date:
    """Zwraca datę n-tego dnia roboczego po from_date (pomija soboty/niedziele)."""
    d = from_date
    count = 0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() < 5:  # pon-pt
            count += 1
    return d


# ── Build agenda ───────────────────────────────────────────────────────────────

def build_agenda(target_date: date) -> dict | None:
    """
    Buduje agendę na codzienny post-reminder o 15:00.
    Zakres: jutro (1 dzień roboczy) + dywidendy do piątku tego tygodnia.

    Zwraca dict z events + dividends, lub None gdy brak danych.
    """
    bq = get_bq_client()

    # Events: jutro (1 dzień roboczy — reminder, nie przegląd)
    date_from = _next_trading_day(target_date, 1)
    date_to = date_from  # ten sam dzień

    # Dividends: od jutra do piątku tego samego tygodnia
    div_from = date_from
    div_to = date_from + timedelta(days=(4 - date_from.weekday()))

    events = bq.load_calendar_events(date_from, date_to)
    dividends = bq.load_upcoming_dividends(div_from, div_to)

    # Filtruj: tylko dywidendy z data_ustalenia w zakresie (odrzuć matchowane po data_wyplaty)
    dividends = [
        d for d in dividends
        if div_from.isoformat() <= d.get("data_ustalenia", "") <= div_to.isoformat()
    ]

    if not events and not dividends:
        logger.info(f"Brak wydarzeń i dywidend na agendę {target_date}")
        return None

    return {
        "target_date": target_date,
        "date_from": date_from,
        "date_to": date_to,
        "events": events,
        "dividends": dividends,
        "event_count": len(events),
        "dividend_count": len(dividends),
    }


def _next_monday(from_date: date) -> date:
    """Zwraca najbliższy poniedziałek po from_date (lub sam from_date jeśli to pon)."""
    days_ahead = (7 - from_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return from_date + timedelta(days=days_ahead)


def build_weekly_agenda(reference_date: date) -> dict | None:
    """
    Buduje agendę na nadchodzący tydzień (pon-pt).
    Wywoływana w niedzielę — patrzy na następny pon-pt.

    Zwraca dict z events + dividends, lub None gdy brak danych.
    """
    bq = get_bq_client()

    monday = _next_monday(reference_date)
    friday = monday + timedelta(days=4)

    events = bq.load_calendar_events(monday, friday)
    dividends = bq.load_upcoming_dividends(monday, friday)

    if not events and not dividends:
        logger.info(f"Brak wydarzeń i dywidend na tydzień {monday}–{friday}")
        return None

    return {
        "target_date": reference_date,
        "date_from": monday,
        "date_to": friday,
        "events": events,
        "dividends": dividends,
        "event_count": len(events),
        "dividend_count": len(dividends),
    }


def build_past_week_dividends(reference_date: date) -> dict | None:
    """
    Buduje retrospekcję dywidend z minionego tygodnia (pon-pt).
    Wywoływana w sobotę — patrzy na miniony pon-pt.

    Zwraca dict z dividends + date range, lub None gdy brak danych.
    """
    bq = get_bq_client()

    # Miniony poniedziałek: cofnij do pon tego tygodnia
    days_since_monday = reference_date.weekday()  # sob=5
    monday = reference_date - timedelta(days=days_since_monday)
    friday = monday + timedelta(days=4)

    dividends = bq.load_upcoming_dividends(monday, friday)

    if not dividends:
        logger.info(f"Brak dywidend w tygodniu {monday}–{friday}")
        return None

    return {
        "target_date": reference_date,
        "date_from": monday,
        "date_to": friday,
        "dividends": dividends,
        "dividend_count": len(dividends),
    }


# ── Format for Gemini prompt ──────────────────────────────────────────────────

def format_agenda_for_prompt(agenda: dict) -> str:
    """Formatuje agendę do tekstu dla Gemini prompt."""
    parts = []

    # ── Events section ──
    if agenda["events"]:
        parts.append("--- WYDARZENIA KORPORACYJNE ---")
        by_date = defaultdict(list)
        for ev in agenda["events"]:
            by_date[ev["data"]].append(ev)

        for d in sorted(by_date.keys()):
            try:
                dt = date.fromisoformat(d)
                day_name = _POLISH_DAYS.get(dt.weekday(), "")
                parts.append(f"\n{d} ({day_name}):")
            except ValueError:
                parts.append(f"\n{d}:")

            for ev in by_date[d]:
                ticker = ev.get("ticker", "")
                typ = ev.get("typ", "")
                opis = ev.get("opis", "")
                if ticker:
                    parts.append(f"  • {ticker}: {opis} [{typ}]")
                else:
                    parts.append(f"  • {opis} [{typ}]")

    # ── Dividends section ──
    if agenda["dividends"]:
        parts.append("\n--- DYWIDENDY (nadchodzące 7 dni) ---")
        for div in agenda["dividends"]:
            ticker = div.get("ticker", "")
            kwota = div.get("dywidenda")
            stopa = div.get("stopa_proc")
            ustalenia = div.get("data_ustalenia", "?")
            wyplaty = div.get("data_wyplaty", "?")

            kwota_str = f"{kwota:.2f} zł" if kwota else "?"
            stopa_str = f"{stopa}%" if stopa else ""
            stopa_part = f" (stopa {stopa_str})" if stopa_str else ""

            parts.append(
                f"  • {ticker}: {kwota_str}/akcję{stopa_part}, "
                f"dzień ustalenia: {ustalenia}, wypłata: {wyplaty}"
            )

    return "\n".join(parts)
