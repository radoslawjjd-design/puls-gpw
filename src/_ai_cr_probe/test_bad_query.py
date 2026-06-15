"""Test for upsert_window — fully mocked, so it never proves the SQL is valid."""
from unittest.mock import MagicMock

from .bad_query import upsert_window


def test_upsert_window_runs():
    client = MagicMock()
    upsert_window(client, "ABC", 5)
    # Asserts only that .query() was called — a mocked client accepts any
    # string, so a reserved-keyword syntax error would still pass this test.
    assert client.query.called
