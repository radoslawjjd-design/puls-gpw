"""Read official quotation feeds without storing rows or sending notifications."""

from datetime import datetime, timezone
import json
import os
from zoneinfo import ZoneInfo

from src.gpw_etf_metrics import fetch_etf_page
from src.gpw_quotations import fetch_quotations

if os.environ["GOOGLE_CLOUD_PROJECT"] != "puls-gpw-kp-20261029":
    raise RuntimeError("Wrong quotation probe project")

for market, minimum in (("gpw", 300), ("nc", 250)):
    rows = fetch_quotations(market)
    print(json.dumps({"migration_probe": "quotations", "market": market, "rows": len(rows), "ok": len(rows) >= minimum}), flush=True)

now = datetime.now(timezone.utc)
instruments, quotes = fetch_etf_page(now.astimezone(ZoneInfo("Europe/Warsaw")).date(), now)
print(json.dumps({"migration_probe": "etf", "instruments": len(instruments), "quotes": len(quotes), "ok": bool(instruments) and bool(quotes)}), flush=True)
