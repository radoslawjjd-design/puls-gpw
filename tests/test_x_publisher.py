"""Unit tests for src/x_publisher.py — no network, fake tweepy.Client."""
import pytest

import src.x_publisher as xp
from src.exceptions import XPublisherError, XPublishPartialError

_CREDS = {
    "X_API_KEY": "key",
    "X_API_SECRET": "secret",
    "X_ACCESS_TOKEN": "token",
    "X_ACCESS_SECRET": "access-secret",
}


class FakeResponse:
    def __init__(self, tid: str):
        self.data = {"id": tid}


class FakeClient:
    """Records create_tweet calls and returns incrementing ids."""

    instances: list["FakeClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list[dict] = []
        self._next = 1000
        # configurable failure: raise on the (1-based) call number `fail_on`
        self.fail_on: int | None = None
        FakeClient.instances.append(self)

    def create_tweet(self, *, text, in_reply_to_tweet_id=None, media_ids=None):
        self.calls.append({
            "text": text,
            "in_reply_to_tweet_id": in_reply_to_tweet_id,
            "media_ids": media_ids,
        })
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise RuntimeError("tweepy boom")
        self._next += 1
        return FakeResponse(str(self._next))


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Reset the module singleton + clear creds before each test."""
    xp._publisher = None
    FakeClient.instances = []
    for var in xp._CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    xp._publisher = None


def _set_creds(monkeypatch, creds=None):
    for var, val in (creds or _CREDS).items():
        monkeypatch.setenv(var, val)


def test_import_does_not_require_creds():
    # Importing the module (done at top) must not have raised; the singleton
    # is lazy. With no creds set, instantiation only fails on the accessor.
    assert xp._publisher is None


def test_missing_creds_fail_fast(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    # only three of four set
    _set_creds(monkeypatch, {k: v for k, v in list(_CREDS.items())[:3]})
    with pytest.raises(XPublisherError) as exc:
        xp.get_x_publisher()
    assert "X_ACCESS_SECRET" in str(exc.value)
    assert FakeClient.instances == []  # client never built


def test_single_tweet_publish(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    _set_creds(monkeypatch)
    ids = xp.get_x_publisher().publish_thread(["just one"])
    assert ids == ["1001"]
    client = FakeClient.instances[0]
    assert client.calls == [
        {"text": "just one", "in_reply_to_tweet_id": None, "media_ids": None}
    ]


def test_thread_reply_chain_ordering(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    _set_creds(monkeypatch)
    ids = xp.get_x_publisher().publish_thread(["hook", "body", "closing"])
    assert ids == ["1001", "1002", "1003"]
    calls = FakeClient.instances[0].calls
    # first tweet is a root, each subsequent replies to the previous id
    assert calls[0]["in_reply_to_tweet_id"] is None
    assert calls[1]["in_reply_to_tweet_id"] == "1001"
    assert calls[2]["in_reply_to_tweet_id"] == "1002"


def test_partial_failure_carries_published_ids(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()
    FakeClient.instances[0].fail_on = 2  # tweet 2 of 3 fails
    with pytest.raises(XPublishPartialError) as exc:
        pub.publish_thread(["hook", "body", "closing"])
    assert exc.value.published_ids == ["1001"]
    assert isinstance(exc.value.cause, RuntimeError)


def test_first_tweet_failure_is_full_failure(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()
    FakeClient.instances[0].fail_on = 1  # first tweet fails → nothing posted
    with pytest.raises(XPublisherError) as exc:
        pub.publish_thread(["hook", "body"])
    # plain XPublisherError, NOT the partial subclass
    assert not isinstance(exc.value, XPublishPartialError)


def test_creds_are_bom_and_whitespace_stripped(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    _set_creds(monkeypatch, {
        "X_API_KEY": "﻿key\n",
        "X_API_SECRET": "  secret  ",
        "X_ACCESS_TOKEN": "﻿token",
        "X_ACCESS_SECRET": "access-secret\r\n",
    })
    xp.get_x_publisher()
    kwargs = FakeClient.instances[0].kwargs
    assert kwargs["consumer_key"] == "key"
    assert kwargs["consumer_secret"] == "secret"
    assert kwargs["access_token"] == "token"
    assert kwargs["access_token_secret"] == "access-secret"


def test_singleton_reused(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    _set_creds(monkeypatch)
    first = xp.get_x_publisher()
    second = xp.get_x_publisher()
    assert first is second
    assert len(FakeClient.instances) == 1  # client built once


# ── publish_thread_with_media (PUL-39) ─────────────────────────────────────────

class FakeOAuth1UserHandler:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class FakeMedia:
    def __init__(self, media_id: str):
        self.media_id = media_id


class FakeAPI:
    """Records media_upload calls; configurable to fail on a given (1-based) call."""

    instances: list["FakeAPI"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.upload_calls: list[str] = []
        self.fail_on: int | None = None
        self._next = 5000
        FakeAPI.instances.append(self)

    def media_upload(self, filename: str):
        self.upload_calls.append(filename)
        if self.fail_on is not None and len(self.upload_calls) == self.fail_on:
            raise RuntimeError("media upload boom")
        self._next += 1
        return FakeMedia(str(self._next))


@pytest.fixture(autouse=True)
def _reset_fake_api():
    FakeAPI.instances = []
    yield
    FakeAPI.instances = []


def _patch_media_doubles(monkeypatch):
    monkeypatch.setattr(xp.tweepy, "Client", FakeClient)
    monkeypatch.setattr(xp.tweepy, "API", FakeAPI)
    monkeypatch.setattr(xp.tweepy, "OAuth1UserHandler", FakeOAuth1UserHandler)


def test_publish_thread_with_media_success_passes_media_ids(monkeypatch):
    _patch_media_doubles(monkeypatch)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()

    result = pub.publish_thread_with_media(
        ["hook", "body"], [["shot1.png"], ["shot2.png"]]
    )

    assert result.tweet_ids == ["1001", "1002"]
    assert result.media_attached == [True, True]
    calls = FakeClient.instances[0].calls
    assert calls[0]["media_ids"] == ["5001"]
    assert calls[1]["media_ids"] == ["5002"]


def test_publish_thread_with_media_multiple_images_per_tweet(monkeypatch):
    _patch_media_doubles(monkeypatch)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()

    result = pub.publish_thread_with_media(
        ["hook"], [["shot1.png", "shot2.png", "shot3.png"]]
    )

    assert result.media_attached == [True]
    assert pub._api_v1.upload_calls == ["shot1.png", "shot2.png", "shot3.png"]
    assert FakeClient.instances[0].calls[0]["media_ids"] == ["5001", "5002", "5003"]


def test_publish_thread_with_media_caps_at_four_images(monkeypatch):
    _patch_media_doubles(monkeypatch)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()

    pub.publish_thread_with_media(
        ["hook"], [["a.png", "b.png", "c.png", "d.png", "e.png"]]
    )

    assert pub._api_v1.upload_calls == ["a.png", "b.png", "c.png", "d.png"]


def test_publish_thread_with_media_upload_failure_falls_back_to_text_only(monkeypatch):
    _patch_media_doubles(monkeypatch)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()
    pub._api_v1.fail_on = 1  # first media_upload call fails

    result = pub.publish_thread_with_media(["hook", "body"], [["bad.png"], []])

    assert result.media_attached == [False, False]
    assert result.tweet_ids == ["1001", "1002"]
    calls = FakeClient.instances[0].calls
    assert calls[0].get("media_ids") is None
    assert calls[1].get("media_ids") is None


def test_publish_thread_with_media_partial_upload_failure_attaches_successful_ones(monkeypatch):
    _patch_media_doubles(monkeypatch)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()
    pub._api_v1.fail_on = 2  # second of three uploads fails

    result = pub.publish_thread_with_media(["hook"], [["a.png", "b.png", "c.png"]])

    assert result.media_attached == [True]
    assert FakeClient.instances[0].calls[0]["media_ids"] == ["5001", "5002"]


def test_publish_thread_with_media_create_tweet_failure_after_media_upload_raises(monkeypatch):
    _patch_media_doubles(monkeypatch)
    _set_creds(monkeypatch)
    pub = xp.get_x_publisher()
    pub._client.fail_on = 1  # first create_tweet call fails -> full failure (0 posted)

    with pytest.raises(XPublisherError) as exc:
        pub.publish_thread_with_media(["hook"], [["shot1.png"]])
    assert not isinstance(exc.value, XPublishPartialError)
    # media upload still happened before the failing create_tweet
    assert pub._api_v1.upload_calls == ["shot1.png"]
