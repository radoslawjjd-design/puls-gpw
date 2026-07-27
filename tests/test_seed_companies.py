"""Guard for scripts/seed_companies.py (PUL-98).

The script reconciles the `companies` table; it must not be a price authority.
It shares the full-upsert primitive and builds rows by splatting `trading_data`,
which never carries `source` or `kurs_odn` — so a run after the daily job would
overwrite both with NULL for every listed ticker. Worse, the failure is swallowed
into a `logger.warning`, so the clobber would not even surface as a non-zero exit.
"""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "seed_companies.py"
_spec = importlib.util.spec_from_file_location("seed_companies", _SCRIPT)
seed = importlib.util.module_from_spec(_spec)
sys.modules["seed_companies"] = seed
_spec.loader.exec_module(seed)


def test_seed_companies_has_no_company_daily_stats_write_path():
    """No name reaching company_daily_stats may be bound in this module."""
    for name in (
        "merge_company_daily_stats",
        "create_company_daily_stats_table_if_not_exists",
        "ensure_company_daily_stats_schema_current",
    ):
        assert not hasattr(seed, name), (
            f"seed_companies imports {name} — it would clobber source/kurs_odn with NULL"
        )
