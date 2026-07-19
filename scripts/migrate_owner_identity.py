"""One-time re-key of the owner's per-user rows: browser UUID → Firebase UID (PUL-74).

Moves watchlist, user_portfolios and user_portfolio_positions rows keyed by the
pre-registration anonymous browser UUID onto the owner's Firebase UID, so the
historical data shows up after e-mail login. The three tables are re-keyed
together in one run — identities must stay in lockstep (positions MERGE key
includes user_id since PUL-74 phase 1).

No DELETEs anywhere; watchlist rows get BOTH user_id and client_id updated
(dual-write consistency until the legacy column is dropped).

HUMAN-RUN ONLY. Usage:
    uv run python scripts/migrate_owner_identity.py \
        --old-uuid <browser-uuid> --new-uid <firebase-uid> --dry-run
    # verify the counts, then re-run without --dry-run

Requires ADC: gcloud auth application-default login
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import argparse

from google.cloud import bigquery

from db.bigquery import (
    _USER_PORTFOLIO_POSITIONS_TABLE_NAME,
    _USER_PORTFOLIOS_TABLE_NAME,
    _WATCHLIST_TABLE_NAME,
    _get_client,
    _table_ref,
)

# (table, predicate column, SET clause) — watchlist re-keys both identity
# columns; portfolio tables have only user_id.
_MIGRATIONS = [
    (_WATCHLIST_TABLE_NAME, "user_id",
     "SET user_id = @new_uid, client_id = @new_uid"),
    (_USER_PORTFOLIOS_TABLE_NAME, "user_id",
     "SET user_id = @new_uid"),
    (_USER_PORTFOLIO_POSITIONS_TABLE_NAME, "user_id",
     "SET user_id = @new_uid"),
]


def _params(old_uuid: str, new_uid: str) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("old_uuid", "STRING", old_uuid),
            bigquery.ScalarQueryParameter("new_uid", "STRING", new_uid),
        ]
    )


def count_rows(client, table: str, column: str, old_uuid: str, new_uid: str) -> int:
    query = f"""
        SELECT COUNT(*) AS n
        FROM `{_table_ref(client, table)}`
        WHERE {column} = @old_uuid
    """
    rows = list(client.query(query, job_config=_params(old_uuid, new_uid)).result())
    return int(rows[0].n)


def rekey_rows(client, table: str, column: str, set_clause: str,
               old_uuid: str, new_uid: str) -> int:
    query = f"""
        UPDATE `{_table_ref(client, table)}`
        {set_clause}
        WHERE {column} = @old_uuid
    """
    job = client.query(query, job_config=_params(old_uuid, new_uid))
    job.result()
    if job.errors:
        raise RuntimeError(f"re-key of {table} failed: {job.errors}")
    return int(job.num_dml_affected_rows or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-uuid", required=True,
                        help="anonymous browser UUID (localStorage watchlist_client_id)")
    parser.add_argument("--new-uid", required=True,
                        help="owner's Firebase UID (users.user_id)")
    parser.add_argument("--dry-run", action="store_true",
                        help="only print per-table matched-row counts")
    args = parser.parse_args(argv)

    if args.old_uuid == args.new_uid:
        print("error: --old-uuid and --new-uid are identical — nothing to migrate")
        return 2

    client = _get_client()

    print(f"{'DRY-RUN' if args.dry_run else 'RE-KEY'}: {args.old_uuid} -> {args.new_uid}")
    total = 0
    for table, column, set_clause in _MIGRATIONS:
        matched = count_rows(client, table, column, args.old_uuid, args.new_uid)
        total += matched
        print(f"  {table}: {matched} row(s) match {column} = old-uuid")
        if not args.dry_run and matched:
            affected = rekey_rows(client, table, column, set_clause,
                                  args.old_uuid, args.new_uid)
            remaining = count_rows(client, table, column, args.old_uuid, args.new_uid)
            print(f"  {table}: re-keyed {affected}, remaining under old-uuid: {remaining}")
            if remaining:
                print(f"error: {table} still has {remaining} row(s) under the old uuid")
                return 1

    if total == 0:
        print("no rows matched the old uuid in any table — check the value")
        return 1 if args.dry_run else 0

    print("done" if not args.dry_run else "dry-run complete — re-run without --dry-run to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
