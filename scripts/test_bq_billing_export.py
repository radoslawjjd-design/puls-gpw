"""Round-trip smoke test for the billing-export read layer (PUL-125).

Verifies against the REAL table that:
- get_billing_rows() parses, and returns the columns the report needs
- get_daily_gross() returns one total per day
- the UNNEST over the `credits` REPEATED RECORD is accepted by BigQuery

Unlike every other test_bq_* script this one is READ-ONLY and uses no sentinel
rows or throwaway tables: the billing export is written by Google, we have no
writer on it, and our DDL must never go near it. Nothing to clean up.

Why it exists at all: mocked unit tests pin the SQL string but never send it to
a parser, and this query is the first in the codebase to UNNEST a table column
(see the reserved-keyword lesson in context/foundation/lessons.md). A syntax
error here would otherwise surface as a 09:00 alert on production.

Run with:
    uv run python scripts/test_bq_billing_export.py

Requires ADC: gcloud auth application-default login
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from db.bigquery import get_billing_rows, get_daily_gross

_EXPECTED_KEYS = {"day", "service", "sku", "gross", "net", "usage_amount", "usage_unit"}


def main() -> None:
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=7)
    print(f"Window: {start} .. {end} (inclusive)\n")

    rows = get_billing_rows(start, end)
    print(f"get_billing_rows: {len(rows)} rows")
    if not rows:
        print("  !! no rows — the export may be empty for this window, or the query is wrong")
        sys.exit(1)

    missing = _EXPECTED_KEYS - set(rows[0])
    if missing:
        print(f"  !! missing keys: {sorted(missing)}")
        sys.exit(1)

    print("  top 5 by gross:")
    for r in sorted(rows, key=lambda r: r["gross"] or 0, reverse=True)[:5]:
        print(
            f"    {r['day']}  {r['service']:<20.20}  {r['sku']:<48.48}  "
            f"gross={r['gross']:>9.4f}  net={r['net']:>9.4f}  "
            f"usage={r['usage_amount']}  unit={r['usage_unit']}"
        )

    totals = get_daily_gross(start, end)
    print(f"\nget_daily_gross: {len(totals)} days")
    for day in sorted(totals):
        print(f"    {day}  {totals[day]:>9.4f}")

    # The two reads must agree — they are separate queries over the same rows,
    # and a mismatch means one of them buckets the day differently.
    for day, total in totals.items():
        from_rows = sum(r["gross"] or 0 for r in rows if r["day"] == day)
        if abs(from_rows - total) > 1e-6:
            print(f"\n  !! {day}: rows sum to {from_rows:.6f} but daily gross is {total:.6f}")
            sys.exit(1)
    print("\nOK — both reads parse and agree on every day in the window.")


if __name__ == "__main__":
    main()
