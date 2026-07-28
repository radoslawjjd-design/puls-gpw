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
    list_companies_with_hop_info,
    merge_company_daily_stats,
)
from src.bankier_metrics import fetch_listing_page, symbol_from_hop_url
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
