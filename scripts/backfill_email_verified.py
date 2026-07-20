"""One-time backfill: emailVerified=True for all pre-gate accounts (PUL-86).

The login gate rejects accounts with emailVerified=false. Every account
registered BEFORE the gate deploys never received a verification mail, so this
script trusts them wholesale: lists all Firebase users and flips email_verified
to True where it is False. Idempotent — a re-run reports 0 to update.

ROLLOUT ORDER (critical): run on prod BEFORE merging the gate to master
(deploy = merge -> CI), or every existing user — owner included — is locked
out. Accounts self-registered between --apply and the deploy get auto-trusted;
accepted at current traffic, re-run if in doubt.

HUMAN-RUN ONLY. Usage:
    uv run python scripts/backfill_email_verified.py            # dry-run
    uv run python scripts/backfill_email_verified.py --apply    # perform writes

Requires FIREBASE_SERVICE_ACCOUNT_JSON in the environment (or .env).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import argparse

from firebase_admin import auth as firebase_auth  # type: ignore[import-untyped]

from src.auth import _get_firebase_app


def _count_unverified() -> int:
    return sum(
        1 for u in firebase_auth.list_users().iterate_all() if not u.email_verified
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="perform the writes (default: dry-run, print only)")
    args = parser.parse_args(argv)

    _get_firebase_app()

    verified_count = 0
    unverified = []
    for user in firebase_auth.list_users().iterate_all():
        if user.email_verified:
            verified_count += 1
        else:
            unverified.append(user)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode}: {verified_count} already verified, {len(unverified)} to update")
    for user in unverified:
        print(f"  {user.email or '<no email>'} ({user.uid})")
        if args.apply:
            firebase_auth.update_user(user.uid, email_verified=True)

    if args.apply:
        remaining = _count_unverified()
        print(f"post-check: {remaining} account(s) still unverified")
        if remaining:
            return 1
        print("done" if unverified else "nothing to do (idempotent re-run)")
        return 0

    print("dry-run complete — re-run with --apply to perform the writes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
