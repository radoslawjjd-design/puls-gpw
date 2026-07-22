import os
import threading
import time
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import uvicorn
from firebase_admin import auth as firebase_admin_auth  # type: ignore[import-untyped]

from src.api import create_app
from src.auth import InvalidCredentialsError


_ADMIN_KEY = "e2e-admin-key"
_USER_KEY  = "e2e-user-key"

# Hasło, które fake verify_password_rest ZAWSZE odrzuca — pozwala e2e pokryć
# ścieżkę 401 "Nieprawidłowy email lub hasło" bez prawdziwego Firebase.
E2E_WRONG_PASSWORD = "ZleHaslo999"

# PUL-83: konto e-mail z rolą admin w fake'owym BQ. Uid MUSI być wyprowadzany
# z e-maila IDENTYCZNIE w obu mockach (verify_password_rest i create_user) —
# inaczej register→login rozjechałby stan portfela/watchlisty na dwa uid-y.
E2E_ADMIN_EMAIL = "admin@example.com"


def _e2e_uid(email):
    return "e2e-uid-" + email


# ── PUL-74: wspólny login e-mailowy dla speców per-user ───────────────────────
# Widoki per-user są JWT-only; fake verify_password_rest przyjmuje dowolne
# email/hasło (uid = "e2e-uid-" + email). Unikalny e-mail per wywołanie izoluje
# stan fake-BQ między testami (stan modułowy przeżywa cały serwer sesyjny).
E2E_PASSWORD = "E2eHaslo123"


def e2e_unique_email() -> str:
    return f"e2e-{time.time_ns()}@example.com"


def e2e_login_email(page, base_url, email=None):
    """Zaloguj przez prawdziwy formularz e-mail; zwraca użyty e-mail."""
    from playwright.sync_api import expect

    email = email or e2e_unique_email()
    page.goto(base_url)
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    form = page.locator("#email-login-form")
    expect(form).to_be_visible()
    form.get_by_label("E-mail").fill(email)
    form.get_by_label("Hasło", exact=True).fill(E2E_PASSWORD)
    form.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")
    return email


def _fake_verify_password_rest(email, password):
    if password == E2E_WRONG_PASSWORD:
        raise InvalidCredentialsError("INVALID_LOGIN_CREDENTIALS")
    return (_e2e_uid(email), email)


def _fake_firebase_create_user(email, password):
    # PUL-86: marker "taken" w e-mailu symuluje 409 (konto już istnieje) —
    # pozwala e2e pokryć hint resend przy rejestracji bez realnego Firebase.
    if "taken" in email:
        raise firebase_admin_auth.EmailAlreadyExistsError("exists", None, None)
    return SimpleNamespace(uid=_e2e_uid(email))


def _fake_firebase_get_user(uid):
    # PUL-86: bramka logowania czyta email_verified przez Admin SDK. Marker
    # "unverified" w e-mailu (a więc i uid) symuluje konto przed kliknięciem
    # linku; wszystkie pozostałe specy logują się jak dotychczas. Jawny
    # SimpleNamespace, nie goły MagicMock — truthy atrybut przeszedłby bramkę
    # przypadkiem (plan-review F3).
    return SimpleNamespace(uid=uid, email_verified="unverified" not in uid)


def _fake_get_user_role(user_id):
    return "admin" if user_id == _e2e_uid(E2E_ADMIN_EMAIL) else "user"

_FAKE_ADMIN_ROWS = [
    {
        "announcement_id": f"id{i}", "url": f"http://example.com/{i}",
        "published_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "title": f"Ann {i}", "company": "PKO SA", "ticker": "PKO",
        "post_text": None, "posted_at": None, "x_post_id": None, "analyzed_at": None,
        "supervisor_attempts": None, "parsed_content": None, "priority": None,
        "structured_analysis": None, "analysis_approved": True,
        "analysis_reject_reason": None, "event_type": "ESPI", "analysis_score": 0.8,
    }
    for i in range(20)
]

_FAKE_X_POSTS_ROWS = [
    {
        "x_post_id": "post-pub-1", "window": "ranek",
        "post_text": "Pierwszy tweet PASSUS\n\nDrugi tweet wątku",
        "tweet_ids": "1111111111,2222222222",
        "posted_at": datetime(2026, 6, 18, 8, 0, 0, tzinfo=timezone.utc),
        "supervisor_attempts": 1, "x_publish_status": "published",
    },
    {
        "x_post_id": "post-partial-1", "window": "poludnie",
        "post_text": "Tweet A częściowy\n\nTweet B bez id\n\nTweet C bez id",
        "tweet_ids": "3333333333",
        "posted_at": datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
        "supervisor_attempts": 2, "x_publish_status": "partial",
    },
    {
        "x_post_id": None, "window": "wieczor",
        "post_text": None, "tweet_ids": None,
        "posted_at": datetime(2026, 6, 18, 18, 0, 0, tzinfo=timezone.utc),
        "supervisor_attempts": 3, "x_publish_status": "failed",
    },
    # Padding rows so the unfiltered AND the `x_publish_status=skipped` filtered
    # count both reach the default page_size (20) — without these, the
    # "Następna" button is permanently disabled (3 rows < 20) and url-routing
    # pagination tests have nothing to click into a real page 2.
    *[
        {
            "x_post_id": f"pad-{i}", "window": "test",
            "post_text": None, "tweet_ids": None,
            "posted_at": datetime(2026, 6, 10, 8, 0, 0, tzinfo=timezone.utc),
            "supervisor_attempts": 1, "x_publish_status": "skipped",
        }
        for i in range(20)
    ],
]


_FAKE_ETF_INSTRUMENTS = [
    {"ticker": "ETFBW20TR", "name": "ETFBW20TR", "instrument_type": "ETF"},
]


_FAKE_TREEMAP_LATEST = {
    "snapshot_id": "snap-e2e-1", "wallet": "main", "snapshot_date": "2026-06-20",
    "total_value": 2000.0, "currency": "PLN",
    "day_change_abs": 100.0, "day_change_pct": 5.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "PKO", "value": 1200.0, "pct": 20.0}, '
        '{"ticker": "CDR", "value": 300.0, "pct": 50.0}, '
        '{"ticker": "NEW", "value": 500.0}'
        '], "media_attached": false}'
    ),
}

_FAKE_TREEMAP_PRIOR = {
    "snapshot_id": "snap-e2e-0", "wallet": "main", "snapshot_date": "2026-06-19",
    "total_value": 1900.0, "currency": "PLN",
    "day_change_abs": 0.0, "day_change_pct": 0.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "PKO", "value": 1000.0}, '
        '{"ticker": "CDR", "value": 400.0}'
        '], "media_attached": false}'
    ),
}


_FAKE_TREEMAP_IKZE_LATEST = {
    "snapshot_id": "snap-e2e-ikze-1", "wallet": "ikze", "snapshot_date": "2026-06-20",
    "total_value": 1000.0, "currency": "PLN",
    "day_change_abs": 50.0, "day_change_pct": 5.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "ALE", "value": 700.0, "pct": 40.0}, '
        '{"ticker": "KGH", "value": 300.0, "pct": 20.0}'
        '], "media_attached": false}'
    ),
}

_FAKE_TREEMAP_IKZE_PRIOR = {
    "snapshot_id": "snap-e2e-ikze-0", "wallet": "ikze", "snapshot_date": "2026-06-19",
    "total_value": 900.0, "currency": "PLN",
    "day_change_abs": 0.0, "day_change_pct": 0.0,
    "positions_json": (
        '{"positions": ['
        '{"ticker": "ALE", "value": 600.0}, '
        '{"ticker": "KGH", "value": 350.0}'
        '], "media_attached": false}'
    ),
}


def _fake_get_latest_snapshot_for_wallet(wallet):
    """Patches get_latest_snapshot_for_wallet (PUL-50) — keyed by wallet so both
    `main` and `ikze` render with their own real e2e fixture data."""
    if wallet == "main":
        return _FAKE_TREEMAP_LATEST
    if wallet == "ikze":
        return _FAKE_TREEMAP_IKZE_LATEST
    return None


def _fake_get_latest_snapshot_before(wallet, before_date):
    """Patches get_latest_snapshot_before (PUL-50) — keyed by wallet so each
    wallet's daily delta is computed against its own prior snapshot."""
    if wallet == "main":
        return _FAKE_TREEMAP_PRIOR
    if wallet == "ikze":
        return _FAKE_TREEMAP_IKZE_PRIOR
    return None


# In-memory watchlist store keyed by the identity string, mirroring the real BQ
# semantics (idempotent add, no-op-safe remove, most-recently-added first).
# Session-scoped like live_server_url; od PUL-74 kluczem jest uid z JWT —
# izolację między testami daje unikalny e-mail per test (e2e_unique_email);
# jedynie stałe konto adminowe (E2E_ADMIN_EMAIL) współdzieli stan przez cały
# przebieg, więc asercje adminowe nie mogą liczyć wierszy watchlisty.
_watchlist_store: dict[str, list[str]] = {}

_FAKE_WATCHLIST_ANNOUNCEMENT = {
    "company": "PKO SA", "ticker": "PKO", "event_type": "ESPI",
    # Real analysis payload so the admin sentiment bar has data to render;
    # dynamic published_at keeps the row inside the bar's 7-day window
    # (a hardcoded date would silently age out and break the assertion).
    "structured_analysis": '{"summary_pl": "Dobre wyniki", "sentiment": "pozytywny"}',
    "published_at": datetime.now(timezone.utc) - timedelta(days=1),
    "analysis_score": 85.0,
}


# Public landing cards (PUL-72). Dynamic published_at keeps rows inside the
# 90-day window; the sentiment key in the raw JSON lets e2e prove it never
# renders on the public cards (the API strips it server-side).
_FAKE_PUBLIC_TOP_ROWS = [
    {"company": "PKO SA", "ticker": "PKO", "title": "Rekordowe wyniki kwartalne",
     "event_type": "wyniki",
     "published_at": datetime.now(timezone.utc) - timedelta(days=1),
     "structured_analysis": '{"summary_pl": "Bank raportuje rekordowy zysk netto.", "sentiment": "pozytywny"}'},
    {"company": "CD Projekt SA", "ticker": "CDR", "title": "Umowa wydawnicza",
     "event_type": "umowa",
     "published_at": datetime.now(timezone.utc) - timedelta(days=2),
     "structured_analysis": '{"summary_pl": "Podpisano znaczącą umowę wydawniczą.", "sentiment": "pozytywny"}'},
    {"company": "XTB SA", "ticker": "XTB", "title": "Dywidenda 2026",
     "event_type": "dywidenda",
     "published_at": datetime.now(timezone.utc) - timedelta(days=3),
     "structured_analysis": '{"summary_pl": "Zarząd rekomenduje wypłatę dywidendy.", "sentiment": "neutralny"}'},
]


def _fake_add_watchlist_ticker(client_id, ticker):
    tickers = _watchlist_store.setdefault(client_id, [])
    if ticker not in tickers:
        tickers.insert(0, ticker)


def _fake_remove_watchlist_ticker(client_id, ticker):
    tickers = _watchlist_store.get(client_id, [])
    if ticker in tickers:
        tickers.remove(ticker)


def _fake_list_watchlist_tickers(client_id):
    return list(_watchlist_store.get(client_id, []))


# In-memory notification-settings store (PUL-81). Keyed by uid; unknown user
# reads the opt-in default, mirroring get_notification_settings.
_notification_store: dict[str, dict] = {}


def _fake_get_notification_settings(user_id):
    return _notification_store.get(
        user_id, {"enabled": False, "email": None, "min_score": 0, "confirmed_at": None}
    )


def _fake_upsert_notification_settings(user_id, email, enabled, min_score=0):
    prior = _notification_store.get(user_id, {})
    _notification_store[user_id] = {
        "enabled": enabled,
        "email": email,
        "min_score": min_score,
        "confirmed_at": (prior.get("confirmed_at") or "2026-07-21T00:00:00+00:00")
        if enabled else prior.get("confirmed_at"),
    }


_portfolio_positions_store: dict[str, list[dict]] = {}

_FAKE_PORTFOLIO_ID = "test-portfolio-glowny-001"
_FAKE_PORTFOLIOS = [
    {
        "portfolio_id": _FAKE_PORTFOLIO_ID,
        "portfolio_type": "glowny",
        "portfolio_name": None,
        "display_order": 1,
        "user_id": "test-client-id",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
]
_FAKE_PORTFOLIO_POSITIONS = [
    {
        "ticker": "PKO", "company_name": "PKO BP", "shares": 100.0,
        "avg_buy_price": 45.0, "current_price": 50.0,
        "daily_change_pct": 1.5, "price_as_of": "2026-06-27",
        # >=2 ascending points → sparkline <svg> renders
        "price_history": [45.0, 47.5, 50.0],
    },
    {
        "ticker": "CDR", "company_name": "CD Projekt", "shares": 10.0,
        "avg_buy_price": 130.0, "current_price": None,
        "daily_change_pct": None, "price_as_of": None,
        # no price_history → '—' fallback
    },
]


def _first_weekdays_of_month(n: int) -> list[date]:
    """Return the first n weekday dates in the current month that are not in the future."""
    import calendar as _cal
    today = date.today()
    y, m = today.year, today.month
    _, last = _cal.monthrange(y, m)
    days = []
    for d in range(1, last + 1):
        dt = date(y, m, d)
        if dt.weekday() < 5 and dt <= today:
            days.append(dt)
            if len(days) >= n:
                break
    return days


_cal_weekdays = _first_weekdays_of_month(3)
_FAKE_CALENDAR_ROWS = [
    {"snapshot_date": _cal_weekdays[0], "portfolio_value": 10300.0,
     "daily_change_pln": 300.0, "prices_found": 2, "total_positions": 2},
    {"snapshot_date": _cal_weekdays[1], "portfolio_value": 10150.0,
     "daily_change_pln": -150.0, "prices_found": 2, "total_positions": 2},
    {"snapshot_date": _cal_weekdays[2], "portfolio_value": 10150.0,
     "daily_change_pln": 0.0, "prices_found": 2, "total_positions": 2},
]


def _fake_get_portfolio_calendar_data(portfolio_id, user_id, year, month):
    if portfolio_id == _FAKE_PORTFOLIO_ID:
        return _FAKE_CALENDAR_ROWS
    return []


_FAKE_HISTORY_ROWS = [
    {"snapshot_date": _cal_weekdays[0], "value_pln": 10000.0, "pnl_pln": 300.0},
    {"snapshot_date": _cal_weekdays[1], "value_pln": 10150.0, "pnl_pln": 450.0},
    {"snapshot_date": _cal_weekdays[2], "value_pln": 10120.0, "pnl_pln": 420.0},
]


def _fake_get_portfolio_history(portfolio_id, user_id, start_date):
    if portfolio_id == _FAKE_PORTFOLIO_ID:
        return _FAKE_HISTORY_ROWS
    return []


def _fake_create_user_portfolio_positions_table_if_not_exists():
    pass


def _fake_ensure_user_portfolio_positions_schema_current():
    pass


def _fake_upsert_user_portfolio_position(user_id, portfolio_id, ticker, company_name, shares, avg_buy_price):
    positions = _portfolio_positions_store.setdefault(user_id, [])
    for p in positions:
        if p["ticker"] == ticker and p.get("portfolio_id") == portfolio_id:
            p.update({"company_name": company_name, "shares": shares, "avg_buy_price": avg_buy_price})
            return
    positions.append({
        "ticker": ticker, "company_name": company_name,
        "shares": shares, "avg_buy_price": avg_buy_price,
        "portfolio_id": portfolio_id,
        "current_price": 52.0, "daily_change_pct": 1.5,
        "price_as_of": "2026-06-27",
    })


def _fake_delete_user_portfolio_position(user_id, portfolio_id, ticker):
    store = _portfolio_positions_store.get(user_id, [])
    _portfolio_positions_store[user_id] = [
        p for p in store if not (p["ticker"] == ticker and p.get("portfolio_id") == portfolio_id)
    ]


def _fake_list_user_portfolio_positions(user_id, portfolio_id=None, include_history=False):
    if portfolio_id == _FAKE_PORTFOLIO_ID:
        # Lazy-init per-user store from static FAKE data on first access so
        # upsert/delete operations in E2E tests actually affect the returned list.
        if user_id not in _portfolio_positions_store:
            _portfolio_positions_store[user_id] = [
                {**p, "portfolio_id": _FAKE_PORTFOLIO_ID}
                for p in _FAKE_PORTFOLIO_POSITIONS
            ]
        rows = [
            p for p in _portfolio_positions_store.get(user_id, [])
            if p.get("portfolio_id") == _FAKE_PORTFOLIO_ID
        ]
    else:
        rows = list(_portfolio_positions_store.get(user_id, []))
    # Mirror production: price_history only travels when the caller opts in
    # (the treemap path calls with include_history=False and must stay lean).
    if not include_history:
        return [{k: v for k, v in r.items() if k != "price_history"} for r in rows]
    return rows


def _fake_create_user_portfolios_table_if_not_exists():
    pass


def _fake_ensure_user_portfolios_schema_current():
    pass


def _fake_list_user_portfolios(user_id):
    return list(_FAKE_PORTFOLIOS)


def _fake_create_user_portfolio(user_id, portfolio_type, portfolio_name):
    return _FAKE_PORTFOLIO_ID


def _fake_delete_user_portfolio(user_id, portfolio_id):
    pass


def _fake_assign_orphan_positions_to_portfolio(user_id, portfolio_id):
    pass


def _fake_list_announcements_for_watchlist(client_id, page=1, page_size=20, from_dt=None, to_dt=None):
    if "PKO" in _watchlist_store.get(client_id, []):
        return [_FAKE_WATCHLIST_ANNOUNCEMENT]
    return []


def _fake_summarize_watchlist_sentiment(user_id, days=7):
    """PUL-87: server-side sentiment summary keyed off the same watchlist store —
    empty before the admin adds PKO, one pozytywny row (score 85) after, mirroring
    _FAKE_WATCHLIST_ANNOUNCEMENT. Keeps test_watchlist_sentiment.py green now that the
    bar reads this endpoint instead of aggregating client-side."""
    now = datetime.now(timezone.utc)
    base = {
        "window_from": (now - timedelta(days=days)).isoformat(),
        "window_to": now.isoformat(),
    }
    if "PKO" not in _watchlist_store.get(user_id, []):
        return {**base, "counts": {"pozytywny": 0, "neutralny": 0, "negatywny": 0},
                "avg_score": None, "days_with_data": 0, "total": 0}
    return {**base, "counts": {"pozytywny": 1, "neutralny": 0, "negatywny": 0},
            "avg_score": 85, "days_with_data": 1, "total": 1}


def _fake_list_watchlist_by_sentiment(user_id, bucket, days=7, limit=200):
    """PUL-87 drill-down: mirror the fake summary — after the admin adds PKO, the
    single pozytywny row shows in the pozytywny bucket only; every bucket is empty
    beforehand. Shares the store with _fake_summarize_watchlist_sentiment so popup
    contents match bar counts, exactly as the real functions guarantee by SQL."""
    if "PKO" not in _watchlist_store.get(user_id, []):
        return []
    if bucket != "pozytywny":
        return []
    return [dict(_FAKE_WATCHLIST_ANNOUNCEMENT)]


def _fake_list_x_posts_admin(
    page=1, page_size=20, window=None, x_publish_status=None,
    post_text=None, from_dt=None, to_dt=None,
):
    """Mirrors list_x_posts_admin's filter semantics so the live E2E server
    exercises real filter/pagination narrowing instead of a fixed payload."""
    rows = _FAKE_X_POSTS_ROWS
    if window:
        rows = [r for r in rows if r["window"] == window]
    if x_publish_status:
        rows = [r for r in rows if r["x_publish_status"] == x_publish_status]
    if post_text:
        rows = [r for r in rows if r["post_text"] and post_text.lower() in r["post_text"].lower()]
    if from_dt:
        rows = [r for r in rows if r["posted_at"] >= from_dt]
    if to_dt:
        rows = [r for r in rows if r["posted_at"] <= to_dt]
    rows = sorted(rows, key=lambda r: r["posted_at"], reverse=True)
    start = (page - 1) * page_size
    return rows[start:start + page_size]


@pytest.fixture(autouse=True)
def _accept_gdpr(page, request):
    """Pre-accept GDPR consent so the banner never blocks existing tests.
    Skip for tests marked @pytest.mark.gdpr — those need the real banner."""
    if "gdpr" not in request.node.keywords:
        page.add_init_script("localStorage.setItem('gdpr_consent_v1', 'accepted')")


@pytest.fixture(scope="session")
def live_server_url():
    os.environ["ADMIN_API_KEY"] = _ADMIN_KEY
    os.environ["USER_API_KEY"]  = _USER_KEY
    os.environ["JWT_SECRET"]    = "e2e-jwt-secret"

    _patches = [
        # PUL-74: specy per-user logują się e-mailem dziesiątki razy z jednego
        # IP (127.0.0.1) — realny limiter (10 loginów/min) dawałby losowe 429.
        # Tylko login: patch rejestracji wyciekałby do unit-testu limitera
        # (fixture sesyjny żyje do końca pełnego przebiegu pytest).
        patch("src.auth._login_rate_limiter"),
        patch("src.api.list_announcements_admin", return_value=_FAKE_ADMIN_ROWS),
        patch("src.api.list_announcements_user", return_value=[]),
        patch("src.api.list_distinct_tickers",            return_value=["CDR", "PKO", "XTB"]),
        patch("src.api.list_distinct_portfolio_tickers",  return_value=["CDR", "ETFBW20TR", "PKO", "XTB"]),
        patch("src.api.list_etf_instruments_for_autocomplete", return_value=_FAKE_ETF_INSTRUMENTS),
        patch("src.api.list_distinct_companies",  return_value=["PKO SA", "CD Projekt SA"]),
        patch("src.api.list_x_posts_admin", side_effect=_fake_list_x_posts_admin),
        patch("src.api.get_latest_snapshot_for_wallet", side_effect=_fake_get_latest_snapshot_for_wallet),
        patch("src.api.get_latest_snapshot_before", side_effect=_fake_get_latest_snapshot_before),
        patch("src.api.get_latest_company_stats_fetched_at", return_value="2026-06-27T09:01:05+00:00"),
        patch("src.api.create_watchlist_table_if_not_exists"),
        patch("src.api.ensure_watchlist_schema_current"),
        patch("src.api.create_companies_table_if_not_exists"),
        patch("src.api.ensure_companies_schema_current"),
        patch("src.api.add_watchlist_ticker", side_effect=_fake_add_watchlist_ticker),
        patch("src.api.remove_watchlist_ticker", side_effect=_fake_remove_watchlist_ticker),
        patch("src.api.list_watchlist_tickers", side_effect=_fake_list_watchlist_tickers),
        patch("src.api.list_announcements_for_watchlist", side_effect=_fake_list_announcements_for_watchlist),
        patch("src.api.summarize_watchlist_sentiment", side_effect=_fake_summarize_watchlist_sentiment),
        patch("src.api.list_watchlist_by_sentiment", side_effect=_fake_list_watchlist_by_sentiment),
        patch("src.api.list_top_announcements_public", return_value=_FAKE_PUBLIC_TOP_ROWS),
        patch(
            "src.api.create_user_portfolio_positions_table_if_not_exists",
            side_effect=_fake_create_user_portfolio_positions_table_if_not_exists,
        ),
        patch(
            "src.api.ensure_user_portfolio_positions_schema_current",
            side_effect=_fake_ensure_user_portfolio_positions_schema_current,
        ),
        patch("src.api.upsert_user_portfolio_position", side_effect=_fake_upsert_user_portfolio_position),
        patch("src.api.delete_user_portfolio_position", side_effect=_fake_delete_user_portfolio_position),
        patch("src.api.list_user_portfolio_positions", side_effect=_fake_list_user_portfolio_positions),
        patch(
            "src.api.create_user_portfolios_table_if_not_exists",
            side_effect=_fake_create_user_portfolios_table_if_not_exists,
        ),
        patch(
            "src.api.ensure_user_portfolios_schema_current",
            side_effect=_fake_ensure_user_portfolios_schema_current,
        ),
        patch("src.api.list_user_portfolios", side_effect=_fake_list_user_portfolios),
        patch("src.api.create_user_portfolio", side_effect=_fake_create_user_portfolio),
        patch("src.api.delete_user_portfolio", side_effect=_fake_delete_user_portfolio),
        patch(
            "src.api.assign_orphan_positions_to_portfolio",
            side_effect=_fake_assign_orphan_positions_to_portfolio,
        ),
        patch(
            "src.api.get_portfolio_calendar_data",
            side_effect=_fake_get_portfolio_calendar_data,
        ),
        patch(
            "src.api.get_portfolio_history",
            side_effect=_fake_get_portfolio_history,
        ),
        patch("src.api.create_users_table_if_not_exists"),
        patch("src.api.ensure_users_schema_current"),
        patch("src.api.create_notification_subscriptions_table_if_not_exists"),
        patch("src.api.ensure_notification_subscriptions_schema_current"),
        patch("src.api.get_notification_settings", side_effect=_fake_get_notification_settings),
        patch("src.api.upsert_notification_settings", side_effect=_fake_upsert_notification_settings),
        # Auth endpoints (PUL-71) — patched at the src.auth import site, not
        # src.api: insert_user/upsert_user_login are imported into src.auth,
        # and _get_firebase_app/verify_password_rest live there.
        patch("src.auth.insert_user"),
        patch("src.auth.upsert_user_login"),
        patch("src.auth.get_user_role", side_effect=_fake_get_user_role),
        patch("src.auth._get_firebase_app"),
        patch(
            "src.auth.firebase_auth.create_user",
            side_effect=_fake_firebase_create_user,
        ),
        patch(
            "src.auth.verify_password_rest",
            side_effect=_fake_verify_password_rest,
        ),
        # PUL-85: reset hasła nie może dotykać realnego Firebase ani SMTP —
        # fake user-check + fake link + no-op mailer; kontrakt 204 identyczny
        # dla każdego e-maila.
        # Jawny SimpleNamespace jak _fake_firebase_get_user (impl-review F1):
        # goły MagicMock miał truthy email_verified, więc żaden e2e resend nie
        # ćwiczył gałęzi background-send; marker "unverified" spina oba fake'i.
        patch(
            "src.auth.firebase_auth.get_user_by_email",
            side_effect=lambda email: SimpleNamespace(
                uid=_e2e_uid(email), email_verified="unverified" not in email
            ),
        ),
        patch(
            "src.auth.firebase_auth.generate_password_reset_link",
            return_value="https://puls-gpw.firebaseapp.com/__/auth/action?mode=resetPassword&oobCode=e2e-fake",
        ),
        patch("src.auth.send_password_reset_email"),
        # PUL-86: rejestracja odpala w tle generate_email_verification_link +
        # branded mail — bez tych patchy realny send_alert może wysłać
        # fałszywy alert SMTP do ownera przy każdym e2e rejestracji.
        patch(
            "src.auth.firebase_auth.generate_email_verification_link",
            return_value="https://puls-gpw.firebaseapp.com/__/auth/action?mode=verifyEmail&oobCode=e2e-fake",
        ),
        patch("src.auth.send_verification_email"),
        patch("src.auth.firebase_auth.get_user", side_effect=_fake_firebase_get_user),
    ]

    with ExitStack() as stack:
        for p in _patches:
            stack.enter_context(p)
        server = uvicorn.Server(
            uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.time() + 5
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"

        yield base_url
        server.should_exit = True
        thread.join(timeout=3)
