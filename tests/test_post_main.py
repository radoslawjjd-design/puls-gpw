"""Unit tests for post_main.py publish wiring — flag, substance guard, orchestration.

All BigQuery/X/email side effects are mocked; no network, no real creds.
"""
from unittest.mock import MagicMock

import pytest

import post_main
from src.exceptions import XPublisherError, XPublishPartialError


# ── is_publishable (substance guard) ──────────────────────────────────────────

_GOOD = ["zerknij przed sesją: 🧵", "$PKO wyniki Q1 ▲ mocne", "$PKO — co dalej?"]


def test_is_publishable_true_for_real_thread():
    assert post_main.is_publishable(_GOOD) is True


def test_is_publishable_false_for_empty_list():
    assert post_main.is_publishable([]) is False


def test_is_publishable_false_for_blank_text():
    assert post_main.is_publishable(["", "   ", "\n"]) is False


def test_is_publishable_false_when_too_few_tweets():
    # hook + closing only, no company body
    assert post_main.is_publishable(["hook 🧵", "$PKO closing"]) is False


def test_is_publishable_false_without_cashtag_in_body():
    assert post_main.is_publishable(["hook 🧵", "wyniki spółki były dobre", "closing"]) is False


def test_is_publishable_false_for_placeholder_marker():
    assert post_main.is_publishable(["hook 🧵", "$PKO brak posta", "closing"]) is False


def test_is_publishable_true_for_brak_postepow_not_a_placeholder():
    """Regression (F1): 'brak postępów' must NOT trip the 'brak posta' marker."""
    thread = ["hook 🧵", "$PKO brak postępów w negocjacjach przejęcia ▼", "$PKO co dalej?"]
    assert post_main.is_publishable(thread) is True


# ── _publish_to_x orchestration ───────────────────────────────────────────────

@pytest.fixture
def bq_mocks(monkeypatch):
    """Patch the BQ/X/email collaborators on post_main; return the mocks."""
    update = MagicMock(name="update_x_post_publish_result")
    already = MagicMock(name="x_post_already_published", return_value=False)
    publisher = MagicMock(name="XPublisher")
    publisher.publish_thread.return_value = ["111", "222", "333"]
    get_pub = MagicMock(name="get_x_publisher", return_value=publisher)
    alert = MagicMock(name="send_alert")

    monkeypatch.setattr(post_main, "update_x_post_publish_result", update)
    monkeypatch.setattr(post_main, "x_post_already_published", already)
    monkeypatch.setattr(post_main, "get_x_publisher", get_pub)
    monkeypatch.setattr(post_main, "send_alert", alert)
    return {
        "update": update, "already": already,
        "publisher": publisher, "get_pub": get_pub, "alert": alert,
    }


def test_flag_off_skips_publish(monkeypatch, bq_mocks):
    monkeypatch.setattr(post_main, "X_AUTO_PUBLISH", False)

    status, ids = post_main._publish_to_x(_GOOD, "poludnie", "xp1")

    assert status == "skipped" and ids is None
    bq_mocks["get_pub"].assert_not_called()
    bq_mocks["publisher"].publish_thread.assert_not_called()
    bq_mocks["update"].assert_called_once_with("xp1", None, "skipped")


def test_flag_on_publishable_publishes(monkeypatch, bq_mocks):
    monkeypatch.setattr(post_main, "X_AUTO_PUBLISH", True)

    status, ids = post_main._publish_to_x(_GOOD, "poludnie", "xp1")

    assert status == "published"
    assert ids == ["111", "222", "333"]
    bq_mocks["publisher"].publish_thread.assert_called_once_with(_GOOD)
    bq_mocks["update"].assert_called_once_with("xp1", ["111", "222", "333"], "published")


def test_flag_on_empty_thread_not_published(monkeypatch, bq_mocks):
    monkeypatch.setattr(post_main, "X_AUTO_PUBLISH", True)
    empty = ["hook 🧵", "", "closing"]  # no cashtag, blank body → not publishable

    status, ids = post_main._publish_to_x(empty, "poludnie", "xp1")

    assert status == "skipped" and ids is None
    bq_mocks["publisher"].publish_thread.assert_not_called()
    bq_mocks["update"].assert_called_once_with("xp1", None, "skipped")


def test_flag_on_already_published_skips(monkeypatch, bq_mocks):
    monkeypatch.setattr(post_main, "X_AUTO_PUBLISH", True)
    bq_mocks["already"].return_value = True

    status, ids = post_main._publish_to_x(_GOOD, "poludnie", "xp1")

    assert status == "skipped" and ids is None
    bq_mocks["publisher"].publish_thread.assert_not_called()
    bq_mocks["update"].assert_called_once_with("xp1", None, "skipped")


def test_partial_failure_records_partial_and_alerts(monkeypatch, bq_mocks):
    monkeypatch.setattr(post_main, "X_AUTO_PUBLISH", True)
    bq_mocks["publisher"].publish_thread.side_effect = XPublishPartialError(
        ["111"], RuntimeError("boom on tweet 2")
    )

    status, ids = post_main._publish_to_x(_GOOD, "poludnie", "xp1")

    assert status == "partial"
    assert ids == ["111"]
    bq_mocks["update"].assert_called_once_with("xp1", ["111"], "partial")
    bq_mocks["alert"].assert_called_once()


def test_full_failure_records_failed_and_alerts(monkeypatch, bq_mocks):
    monkeypatch.setattr(post_main, "X_AUTO_PUBLISH", True)
    bq_mocks["publisher"].publish_thread.side_effect = XPublisherError("nothing posted")

    status, ids = post_main._publish_to_x(_GOOD, "poludnie", "xp1")

    assert status == "failed" and ids is None
    bq_mocks["update"].assert_called_once_with("xp1", None, "failed")
    bq_mocks["alert"].assert_called_once()


def test_publish_never_raises_even_if_persist_fails(monkeypatch, bq_mocks):
    """Publishing must never raise out of the approved branch (email must still send)."""
    monkeypatch.setattr(post_main, "X_AUTO_PUBLISH", True)
    bq_mocks["publisher"].publish_thread.side_effect = XPublisherError("boom")
    bq_mocks["update"].side_effect = RuntimeError("BQ down")

    status, ids = post_main._publish_to_x(_GOOD, "poludnie", "xp1")

    assert status == "failed" and ids is None
    bq_mocks["alert"].assert_called_once()
