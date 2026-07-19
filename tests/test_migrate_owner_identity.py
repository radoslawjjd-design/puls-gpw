"""Unit tests for scripts/migrate_owner_identity.py (PUL-74 phase 5).

Mocked BQ client only — no real BigQuery access. Query-shape string asserts
per the project lesson (mocked tests never hit the SQL parser).
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPT = Path(__file__).parent.parent / "scripts" / "migrate_owner_identity.py"
_spec = importlib.util.spec_from_file_location("migrate_owner_identity", _SCRIPT)
mig = importlib.util.module_from_spec(_spec)
sys.modules["migrate_owner_identity"] = mig
_spec.loader.exec_module(mig)

_OLD = "11111111-1111-1111-1111-111111111111"
_NEW = "fb-uid-owner"


def _row(n):
    r = MagicMock()
    r.n = n
    return r


def _mk_client(counts):
    """Mock BQ client; successive COUNT queries return values from `counts`."""
    client = MagicMock()
    client.project = "test-project"
    count_iter = iter(counts)

    def _query(q, job_config=None):
        job = MagicMock()
        job.errors = None
        if "SELECT COUNT" in q:
            job.result.return_value = [_row(next(count_iter))]
        else:
            job.result.return_value = None
            job.num_dml_affected_rows = 2
        return job

    client.query.side_effect = _query
    return client


def _queries(client):
    return [c.args[0] for c in client.query.call_args_list]


def _all_params(client):
    out = []
    for c in client.query.call_args_list:
        jc = c.kwargs["job_config"]
        out.append({p.name: p.value for p in jc.query_parameters})
    return out


def test_dry_run_issues_only_count_selects():
    client = _mk_client(counts=[1, 2, 3])
    with patch.object(mig, "_get_client", return_value=client):
        rc = mig.main(["--old-uuid", _OLD, "--new-uid", _NEW, "--dry-run"])

    assert rc == 0
    qs = _queries(client)
    assert len(qs) == 3
    assert all("SELECT COUNT" in q for q in qs)
    assert not any("UPDATE" in q for q in qs)
    for params in _all_params(client):
        assert params["old_uuid"] == _OLD
        assert params["new_uid"] == _NEW


def test_real_run_issues_three_scoped_updates():
    # per table: matched-count, then post-update remaining-count (0)
    client = _mk_client(counts=[2, 0, 1, 0, 3, 0])
    with patch.object(mig, "_get_client", return_value=client):
        rc = mig.main(["--old-uuid", _OLD, "--new-uid", _NEW])

    assert rc == 0
    updates = [q for q in _queries(client) if "UPDATE" in q]
    assert len(updates) == 3
    watchlist_q = next(q for q in updates if "watchlist" in q)
    assert "SET user_id = @new_uid, client_id = @new_uid" in watchlist_q
    for q in updates:
        assert "WHERE user_id = @old_uuid" in q
        assert "DELETE" not in q
    portfolio_qs = [q for q in updates if "watchlist" not in q]
    assert len(portfolio_qs) == 2
    assert all("client_id" not in q for q in portfolio_qs)


def test_identical_ids_refused_without_queries():
    client = _mk_client(counts=[])
    with patch.object(mig, "_get_client", return_value=client):
        rc = mig.main(["--old-uuid", _OLD, "--new-uid", _OLD])

    assert rc == 2
    client.query.assert_not_called()


def test_real_run_fails_when_rows_remain_under_old_uuid():
    # first table: 2 matched, 1 still remaining after the UPDATE → exit 1
    client = _mk_client(counts=[2, 1])
    with patch.object(mig, "_get_client", return_value=client):
        rc = mig.main(["--old-uuid", _OLD, "--new-uid", _NEW])

    assert rc == 1


def test_dry_run_with_zero_matches_signals_wrong_uuid():
    client = _mk_client(counts=[0, 0, 0])
    with patch.object(mig, "_get_client", return_value=client):
        rc = mig.main(["--old-uuid", _OLD, "--new-uid", _NEW, "--dry-run"])

    assert rc == 1
