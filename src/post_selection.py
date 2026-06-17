"""Pure selection logic for the morning X-post pipeline.

Dependency-light leaf module (imports only ``json5`` + ``logging``) so the
data-access layer (``db/bigquery.py``) can call it without dragging the
``google.genai`` import chain (``post_generator`` → ``gemini_client``) into the
DB layer.

Two coupled defects this module fixes (PUL-40):
  - Company starvation — ``LIMIT N`` applied to raw rows before ticker dedup, so
    N raw rows ≠ N distinct companies. Here dedup happens before the top-N cut.
  - Number-less results posts — ``wyniki_finansowe`` / ``wyniki_sprzedazowe``
    rows whose ``key_numbers`` is empty are dropped *before* the cut, so a freed
    slot backfills with the next-best company. Qualitative events
    (``kontrakt_znaczacy``, ``dywidenda``, …) with empty ``key_numbers`` are kept.
"""
import logging

import json5

logger = logging.getLogger(__name__)

# Event types whose posts are meaningless without amounts — an empty
# ``key_numbers`` disqualifies them. Exported so the publish belt in post_main
# imports the same set.
NUMBER_DEPENDENT_EVENT_TYPES = {"wyniki_finansowe", "wyniki_sprzedazowe"}


def _key_numbers(row: dict) -> list:
    """Parse ``structured_analysis`` and return ``key_numbers`` (empty on failure).

    Mirrors the tolerant handling in ``post_generator.generate_post``: a parse
    failure is treated as empty ``key_numbers``.
    """
    raw = row.get("structured_analysis")
    if not raw:
        return []
    try:
        structured = json5.loads(raw)
    except Exception:
        logger.warning(
            "post_selection: failed to parse structured_analysis for %s",
            row.get("announcement_id"),
        )
        return []
    return structured.get("key_numbers") or []


def select_top_companies(rows: list[dict], n: int) -> list[dict]:
    """Select up to ``n`` distinct-company announcements from pre-sorted ``rows``.

    Input contract: ``rows`` is already ordered by selection priority
    (``analysis_score DESC, published_at DESC``). This function is
    order-preserving and performs first-occurrence dedup — it must NOT re-sort.

    Sequence: dedup by ticker → drop empty-``key_numbers`` ``wyniki_*`` rows →
    take first ``n``. Dropping happens *before* the cut so a removed results row
    frees its slot for the next company. Rows without a ``ticker`` are skipped
    (mirrors ``generate_post``).

    Pure: no I/O. Returns a sublist of the input dicts (same objects).
    """
    seen_tickers: set[str] = set()
    selected: list[dict] = []
    for row in rows:
        ticker = row.get("ticker") or ""
        if not ticker:
            logger.info(
                "post_selection: skipping no-ticker row %s", row.get("announcement_id")
            )
            continue
        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        if row.get("event_type") in NUMBER_DEPENDENT_EVENT_TYPES and not _key_numbers(row):
            logger.info(
                "post_selection: dropping number-less %s row for ticker %s",
                row.get("event_type"),
                ticker,
            )
            continue
        selected.append(row)
        if len(selected) >= n:
            break
    return selected
