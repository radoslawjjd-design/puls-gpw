"""Cloud Run Job entrypoint for the daily company-stats snapshot ingestion pipeline."""
import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

from src.logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from db.bigquery import (
    create_company_daily_stats_table_if_not_exists,
    ensure_company_daily_stats_schema_current,
    get_previous_session_closes,
    list_companies_with_hop_info,
    merge_company_daily_stats,
    merge_company_daily_stats_close_correction,
)
from src.bankier_metrics import fetch_listing_page, symbol_from_hop_url
from src.gpw_archive import fetch_archive_session
from src.gpw_quotations import fetch_quotations
from src.notifier import send_alert

WARSAW = ZoneInfo("Europe/Warsaw")

# Minimum plausible instrument count per official feed. Observed 2026-07-27:
# 372 on the GPW main market, 332 on NewConnect. The floors sit well below those
# so a normal session never trips them, while a hollow HTTP 200 or a half-parsed
# table does — `if not rows` cannot tell a dead feed from a healthy partial one
# now that 704 of 744 companies is the normal coverage.
MIN_GPW_ROWS = 300
MIN_NC_ROWS = 250

# Feed keys that describe the instrument rather than its trading day.
_NON_PRICE_KEYS = ("company_name", "isin")

_OFFICIAL_MARKETS = ("gpw", "nc")
_BANKIER_MARKETS = ("akcje", "new-connect")

# Provenance written by the self-heal; distinct from the live feeds so a corrected
# row is identifiable after the fact.
_ARCHIVE_SOURCE = "archive"

# Closes carry four decimals. Anything at or below this is float noise, not a
# disagreement — the defect this detects is 0.1–0.4%, three orders of magnitude larger.
_CLOSE_EPSILON = 1e-4


def _fetch_official_quotations() -> dict[str, tuple[dict, str]]:
    """Return {ticker: (stats, source)} from both official feeds, GPW taking priority.

    Raises when a feed returns implausibly few instruments, which the caller turns
    into an alert and a non-zero exit. Aborting is deliberate: a half-dead feed that
    still answers 200 would otherwise overwrite good closes with nothing.
    """
    floors = {"gpw": MIN_GPW_ROWS, "nc": MIN_NC_ROWS}
    quotations: dict[str, dict[str, dict]] = {}
    for market in _OFFICIAL_MARKETS:
        quotes = fetch_quotations(market)
        if len(quotes) < floors[market]:
            raise RuntimeError(
                f"{market} quotations returned {len(quotes)} instruments, "
                f"below the floor of {floors[market]} — aborting to preserve existing data"
            )
        quotations[market] = quotes

    official: dict[str, tuple[dict, str]] = {}
    for market in _OFFICIAL_MARKETS:
        for ticker, stats in quotations[market].items():
            official.setdefault(ticker, (stats, market))

    logger.info(
        "company_stats_main: official feeds — GPW=%d NC=%d unique=%d",
        len(quotations["gpw"]), len(quotations["nc"]), len(official),
    )
    return official


def _isin_conflict(company: dict, stats: dict) -> bool:
    """True when both sides carry an ISIN and they disagree.

    Zero conflicts exist across the 697 matched companies, so an occurrence means
    the ticker was reused or the identity join drifted — either way, writing a price
    against the wrong company is the failure class this change exists to remove.
    """
    company_isin, feed_isin = company.get("isin"), stats.get("isin")
    if not company_isin or not feed_isin or company_isin == feed_isin:
        return False
    logger.warning(
        "company_stats_main: ISIN conflict for ticker=%s (companies=%s feed=%s) — skipping",
        company["ticker"], company_isin, feed_isin,
    )
    return True


def _build_row(
    ticker: str, stats: dict, source: str, snapshot_date, fetched_at: str
) -> dict:
    row = {
        "ticker": ticker,
        "snapshot_date": snapshot_date.isoformat(),
        "fetched_at": fetched_at,
        "source": source,
        **{k: v for k, v in stats.items() if k not in _NON_PRICE_KEYS},
    }
    # bankier publishes no reference price; leave it NULL rather than invent one.
    row.setdefault("kurs_odn", None)
    return row


def _gap_fill_from_bankier(
    unresolved: list[dict], snapshot_date, fetched_at: str
) -> list[dict]:
    """Price the companies neither official feed lists — ~47 today, ≥18 still traded.

    Only reached when something is actually unresolved, and only ever for tickers
    the official feeds did not supply, so a bankier close can never displace an
    official one.
    """
    if not unresolved:
        return []

    listing: dict[str, dict] = {}
    for market in _BANKIER_MARKETS:
        listing.update(fetch_listing_page(market))
    logger.info(
        "company_stats_main: bankier gap-fill — %d companies unresolved, listing=%d symbols",
        len(unresolved), len(listing),
    )

    rows = []
    for company in unresolved:
        hop_url = company.get("hop_url")
        symbol = symbol_from_hop_url(hop_url) if hop_url else None
        stats = listing.get(symbol) if symbol else None
        if stats is None:
            continue
        rows.append(_build_row(company["ticker"], stats, "bankier", snapshot_date, fetched_at))
    return rows


def _self_heal_previous_session(
    official: dict[str, tuple[dict, str]], today, fetched_at: str
) -> None:
    """Repair the previous session's closes where today's reference price disagrees.

    `kurs_odn` is a **detector only, never a value source.** GPW's reference price
    is the previous close adjusted for corporate actions, so on an ex-dividend or
    split date it legitimately differs from what we stored — 41 of 5 230 comparisons
    diverged by more than 0.3 pp, up to 11 pp. Writing it into `kurs_zamkniecia`
    would reintroduce exactly the dividend-adjustment defect this change removes
    (GH #191). So a disagreement only buys **one** archive fetch, and the archive
    decides.

    Scope is the single most recent session before today, so this can never fight
    the historical corrective pass. Idempotent: a repaired session produces no
    mismatches on the next run.
    """
    session_date, stored = get_previous_session_closes(today)
    if session_date is None:
        logger.info("self_heal: no earlier session to reconcile")
        return

    suspect = {
        ticker: close
        for ticker, close in stored.items()
        if close is not None
        and (entry := official.get(ticker)) is not None
        and entry[0].get("kurs_odn") is not None
        and abs(entry[0]["kurs_odn"] - close) > _CLOSE_EPSILON
    }
    if not suspect:
        logger.info("self_heal: %s reconciles against the reference price", session_date)
        return

    logger.info(
        "self_heal: %d of %d tickers disagree with the reference price for %s — "
        "consulting the archive",
        len(suspect), len(stored), session_date,
    )
    sheet = fetch_archive_session(session_date)
    if not sheet:
        logger.warning("self_heal: archive reports no session on %s — nothing to do", session_date)
        return

    # The archive keys rows by the exchange's abbreviated name; the live feed's
    # company_name is the only bridge back to a ticker. ~10% never map.
    ticker_of = {
        stats["company_name"]: ticker
        for ticker, (stats, _) in official.items()
        if stats.get("company_name")
    }

    corrections: list[dict] = []
    confirmed = 0
    for name, archived in sheet.items():
        ticker = ticker_of.get(name)
        if ticker is None or ticker not in suspect:
            continue
        archived_close = archived.get("kurs_zamkniecia")
        if archived_close is None:
            continue
        if abs(archived_close - suspect[ticker]) <= _CLOSE_EPSILON:
            # The archive confirms what we stored: the reference price moved for a
            # corporate action, not because our close was wrong.
            confirmed += 1
            continue
        pct = archived.get("zmiana_procentowa")
        corrections.append({
            "ticker": ticker,
            "snapshot_date": session_date.isoformat(),
            "kurs_zamkniecia": archived_close,
            "zmiana_procentowa": pct,
            # Derived from the archive's own two numbers rather than from a
            # neighbouring session, so a correction never depends on a row that
            # may itself still be wrong.
            "zmiana_kwotowa": (
                archived_close - archived_close / (1 + pct / 100)
                if pct is not None and pct != -100
                else None
            ),
            "source": _ARCHIVE_SOURCE,
            "fetched_at": fetched_at,
        })
    # Everything the archive could not answer for: a name that does not map back to
    # a ticker (~10%) or a row the archive itself leaves blank.
    unresolved = len(suspect) - len(corrections) - confirmed

    if not corrections:
        logger.info(
            "self_heal: %s needs no correction — %d reference divergences confirmed by the "
            "archive, %d unresolved", session_date, confirmed, unresolved,
        )
        return

    corrected = merge_company_daily_stats_close_correction(corrections)
    logger.info(
        "self_heal: %s — %d closes corrected from the archive (%d rows affected), "
        "%d reference divergences ignored, %d unresolved",
        session_date, len(corrections), corrected, confirmed, unresolved,
    )


def main() -> None:
    try:
        create_company_daily_stats_table_if_not_exists()
        ensure_company_daily_stats_schema_current()
        companies = list_companies_with_hop_info()

        official = _fetch_official_quotations()

        snapshot_date = datetime.now(WARSAW).date()
        fetched_at = datetime.now(timezone.utc).isoformat()

        rows: list[dict] = []
        unresolved: list[dict] = []
        conflicts = 0

        for company in companies:
            entry = official.get(company["ticker"])
            if entry is None:
                unresolved.append(company)
                continue
            stats, source = entry
            if _isin_conflict(company, stats):
                conflicts += 1
                continue
            rows.append(_build_row(company["ticker"], stats, source, snapshot_date, fetched_at))

        gap_filled = _gap_fill_from_bankier(unresolved, snapshot_date, fetched_at)
        rows.extend(gap_filled)

        if not rows:
            raise RuntimeError(
                f"no rows built for {snapshot_date} — aborting to preserve existing data"
            )

        merge_company_daily_stats(rows)

        # Yesterday's repair must never cost today's prices: this runs after the
        # write and swallows its own failures.
        try:
            _self_heal_previous_session(official, snapshot_date, fetched_at)
        except Exception:
            logger.exception("company_stats_main: self-heal failed — ingest unaffected")

        logger.info(
            "company_stats_main: done — official=%d bankier=%d isin_conflicts=%d "
            "unpriced=%d total_companies=%d",
            len(rows) - len(gap_filled), len(gap_filled), conflicts,
            len(unresolved) - len(gap_filled), len(companies),
        )

    except Exception as exc:
        logger.exception("company_stats_main: pipeline failed")
        try:
            send_alert(exc)
            logger.info("company_stats_main: alert email sent")
        except Exception as alert_exc:
            logger.error("company_stats_main: failed to send alert: %s", alert_exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
