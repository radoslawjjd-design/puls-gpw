"""
Centralny helper dla strefy czasowej Europe/Warsaw.

Cloud Run kontenery mają TZ=UTC. Naked `date.today()` / `datetime.now()`
zwracają UTC date/time, co powoduje błędy:
- Joby nocne (00:05 PL = 22:05 UTC zima) → log file "yesterday" zamiast "today"
- Snapshoty BQ z UTC date → przesunięcie dziennej granicy

Zgodnie z CLAUDE.md "Timezone: zawsze Europe/Warsaw (nigdy UTC)".

Użycie:
    from utils.timezone import now_warsaw, today_warsaw

    log_file = f"job_{today_warsaw().strftime('%Y-%m-%d')}.log"
    timestamp = now_warsaw().isoformat(timespec="seconds")
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")


def now_warsaw() -> datetime:
    """Aktualny czas w strefie Europe/Warsaw (timezone-aware)."""
    return datetime.now(tz=WARSAW)


def today_warsaw() -> date:
    """Aktualna data w strefie Europe/Warsaw."""
    return now_warsaw().date()
