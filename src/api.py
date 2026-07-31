import asyncio
import logging
import os
import pathlib
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

import json5
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    UploadFile,
)
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from db.bigquery import BigQueryError  # type: ignore[attr-defined]
from db.bigquery import (
    add_watchlist_ticker,
    assign_orphan_positions_to_portfolio,
    create_companies_table_if_not_exists,
    create_user_portfolio,
    create_user_portfolio_positions_table_if_not_exists,
    create_user_portfolios_table_if_not_exists,
    create_users_table_if_not_exists,
    create_watchlist_table_if_not_exists,
    create_notification_subscriptions_table_if_not_exists,
    create_user_broker_operations_table_if_not_exists,
    ensure_notification_subscriptions_schema_current,
    ensure_user_broker_operations_schema_current,
    get_notification_settings,
    upsert_notification_settings,
    delete_announcement,
    CASH_TICKER,
    delete_user_portfolio,
    delete_user_portfolio_position,
    delete_user_portfolio_positions,
    get_dividend_summary,
    merge_user_broker_operations,
    merge_user_portfolio_positions_bulk,
    ensure_companies_schema_current,
    ensure_user_portfolio_positions_schema_current,
    ensure_user_portfolios_schema_current,
    ensure_users_schema_current,
    ensure_watchlist_schema_current,
    get_latest_company_stats_fetched_at,
    get_latest_snapshot_before,
    get_latest_snapshot_for_wallet,
    list_announcements_admin,
    list_announcements_for_watchlist,
    list_announcements_user,
    list_distinct_companies,
    list_distinct_tickers,
    list_distinct_portfolio_tickers,
    list_etf_instruments_for_autocomplete,
    list_top_announcements_public,
    list_broker_trades,
    list_user_portfolio_positions,
    get_portfolio_calendar_data,
    get_portfolio_history,
    get_portfolio_inception,
    list_user_portfolios,
    list_watchlist_by_sentiment,
    list_watchlist_tickers,
    list_x_posts_admin,
    remove_watchlist_ticker,
    summarize_watchlist_sentiment,
    upsert_user_portfolio_position,
)
from src.auth import refresh_session_if_stale, session_payload_from_request
from src.auth import router as auth_router
from src.brokers import BrokerImportError, get_parser
from src.brokers.xtb import normalize_ticker
from src.portfolio_calendar import compute_calendar_pnl
from src.portfolio_realized import compute_realized_pnl
from src.portfolio_treemap import compute_treemap_positions, compute_user_portfolio_treemap_positions

_AC_CACHE: dict[str, tuple[list, float]] = {}
_AC_TTL = 300  # 5 minutes


def _ac_get(key: str) -> list | None:
    if key in _AC_CACHE:
        data, ts = _AC_CACHE[key]
        if time.time() - ts < _AC_TTL:
            return data
    return None


def _ac_set(key: str, data: list) -> None:
    _AC_CACHE[key] = (data, time.time())


_PERF_CACHE: dict[str, tuple[Any, float]] = {}


def _perf_get(key: str, ttl: int) -> Any | None:
    if key in _PERF_CACHE:
        data, ts = _PERF_CACHE[key]
        if time.time() - ts < ttl:
            return data
    return None


def _perf_set(key: str, data: Any) -> None:
    _PERF_CACHE[key] = (data, time.time())


def _perf_invalidate_portfolio(user_id: str, portfolio_id: str) -> None:
    # PUL-95: a write to one wallet also changes what the "Wszystkie" aggregate and
    # the value chart show, so the sentinel variants and the history prefix have to
    # go too. Both gaps predate the import — it just makes them visible, because the
    # user lands back on the default tab the instant a commit succeeds.
    _PERF_CACHE.pop(f"treemap:{user_id}", None)
    for scope in (portfolio_id, _ALL_PORTFOLIOS):
        _PERF_CACHE.pop(f"positions:{user_id}:{scope}", None)
    # calendar and history both branch on a trailing segment (month, range), so
    # they need a prefix scan rather than a single key.
    prefixes = [f"dividends:{user_id}:", f"realized:{user_id}:"]
    for scope in (portfolio_id, _ALL_PORTFOLIOS):
        prefixes += [f"calendar:{user_id}:{scope}:", f"history:{user_id}:{scope}:"]
    for k in [k for k in _PERF_CACHE if any(k.startswith(p) for p in prefixes)]:
        _PERF_CACHE.pop(k, None)


def _invalidate_wl_sentiment(user_id: str) -> None:
    # PUL-87: the sentiment bar + drill-down are per-user caches; a watchlist
    # add/remove changes what they aggregate, so drop them immediately (the bar
    # is refetched right after a mutation and must reflect the new watchlist).
    _PERF_CACHE.pop(f"wl-sentiment-sum:{user_id}", None)
    prefix = f"wl-sentiment-list:{user_id}:"
    for k in [k for k in _PERF_CACHE if k.startswith(prefix)]:
        _PERF_CACHE.pop(k, None)


# PUL-87 drill-down: the three normalized buckets the bar renders. An unknown path
# segment is a client error (422), not a silent empty list.
_SENTIMENT_BUCKETS = ("pozytywny", "neutralny", "negatywny")
# Row cap for a single bucket's drill-down list. Passed to the BQ fn so `truncated`
# reflects the exact bound the query used (stays in sync if the cap ever changes).
_WL_SENTIMENT_LIST_CAP = 200


_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
Role = Literal["admin", "user"]


def _get_role(
    request: Request,
    response: Response,
    key: str | None = Security(_API_KEY_HEADER),
) -> Role:
    # Order per PUL-71: valid JWT cookie → user; invalid/expired cookie falls
    # through to the API-key headers (a stale browser cookie must not break API-key auth)
    payload = session_payload_from_request(request)
    if payload is not None:
        refresh_session_if_stale(response, payload)
        # PUL-83: role comes from the signed claim; anything but the exact
        # "admin" value (missing claim, legacy token, garbage) degrades to user.
        return "admin" if payload.get("role") == "admin" else "user"
    if key == os.environ.get("ADMIN_API_KEY"):
        return "admin"
    if key == os.environ.get("USER_API_KEY"):
        return "user"
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _require_admin(role: Role = Depends(_get_role)) -> Role:
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return role


def _get_user_id(request: Request) -> str:
    # PUL-74: per-user endpoints are JWT-only — identity comes exclusively from
    # the signed session cookie; the anonymous X-Client-Id path is retired.
    payload = session_payload_from_request(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="Valid session required")
    return payload["user_id"]


def _get_user_email(request: Request) -> str | None:
    # Notification address is the verified account email from the JWT claim —
    # never a client-supplied value. None when the (legacy) token lacks it.
    payload = session_payload_from_request(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="Valid session required")
    return payload.get("email")


def _parse_structured_analysis(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        return json5.loads(raw)
    except Exception:
        return None


class AnnouncementAdmin(BaseModel):
    model_config = ConfigDict(extra="ignore")
    announcement_id: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    title: str | None = None
    company: str | None = None
    ticker: str | None = None
    post_text: str | None = None
    posted_at: datetime | None = None
    x_post_id: str | None = None
    analyzed_at: datetime | None = None
    supervisor_attempts: int | None = None
    parsed_content: str | None = None
    priority: str | None = None
    structured_analysis: dict | None = None
    analysis_approved: bool | None = None
    analysis_reject_reason: str | None = None
    event_type: str | None = None
    analysis_score: float | None = None


class XPostAdmin(BaseModel):
    model_config = ConfigDict(extra="ignore")
    x_post_id: str | None = None
    window: str | None = None
    post_text: str | None = None
    tweet_ids: str | None = None
    posted_at: datetime | None = None
    supervisor_attempts: int | None = None
    x_publish_status: str | None = None


class TreemapPosition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticker: str
    position_value_pln: float | None = None
    daily_change_pln: float | None = None
    daily_change_pct: float | None = None
    portfolio_share_pct: float | None = None
    since_purchase_pct: float | None = None
    since_purchase_pln: float | None = None


_TREEMAP_WALLETS = ("main", "ikze")

# PUL-115: how far back GET /api/portfolio/calendar will look. A guard against a
# hand-edited URL asking for the year 1200, not a claim about how old a wallet
# can be — the picker's own floor is the wallet's inception.
_CALENDAR_MAX_YEARS_BACK = 20


class PortfolioCalendarDay(BaseModel):
    model_config = ConfigDict(extra="ignore")
    date: str
    day: int
    weekday: int
    state: str
    portfolio_value: float | None = None
    pnl_abs: float | None = None
    prices_found: int = 0
    total_positions: int = 0
    mtd_diff: float | None = None


class PortfolioCalendarResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    year: int
    month: int
    days: list[PortfolioCalendarDay]
    # PUL-115: the day this wallet began (ISO), so the month/year picker can offer
    # the months that exist instead of a flat ten years. None when unknown.
    inception: str | None = None


class PortfolioHistoryPoint(BaseModel):
    # FARO-5 (PUL-79): one point on the portfolio value-over-time line.
    model_config = ConfigDict(extra="ignore")
    date: str  # ISO YYYY-MM-DD (trading day)
    value_pln: float
    pnl_pln: float


class PortfolioHistoryNote(BaseModel):
    # PUL-100: a holding whose pre-debut days were valued at its first known close.
    model_config = ConfigDict(extra="ignore")
    ticker: str
    # First date we hold data for — NOT necessarily a listing date. A ticker whose
    # history simply starts when the scraper did looks identical here, so anything
    # rendered from this must say "no quotes before X", never "listed on X".
    listed_from: str  # ISO YYYY-MM-DD
    price: float


class PortfolioHistoryResponse(BaseModel):
    # PUL-100: the series alone can't say which holdings were valued at a debut price
    # or dropped for having no price at all — in a finance app that assumption has to
    # travel with the data, so the endpoint returns an envelope rather than a bare list.
    model_config = ConfigDict(extra="ignore")
    series: list[PortfolioHistoryPoint]
    notes: list[PortfolioHistoryNote] = []
    excluded: list[str] = []
    # PUL-103: the day the series really starts, when the wallet's inception falls inside
    # the requested range. Not a note — a note is per-ticker, this is per-portfolio — but
    # it renders in the same (i) popover. The X axis is index-based, so without it two
    # months of history inside a 1y range looks exactly like a full year.
    data_from: str | None = None  # ISO YYYY-MM-DD


# Supported history ranges → day-based floor from today. `1d` is intentionally
# absent (no intraday data stored); the endpoint 422s on anything not here.
_HISTORY_RANGE_DAYS = {"1w": 7, "1m": 30, "3m": 90, "1y": 365}


def _history_start_date(range_: str) -> date | None:
    """Resolve a history range string to a start date, or None if unsupported."""
    days = _HISTORY_RANGE_DAYS.get(range_)
    return date.today() - timedelta(days=days) if days is not None else None


class ImportPositionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticker: str
    company_name: str | None = None
    shares: float
    avg_buy_price: float
    # True when the ticker is absent from `companies`/`etf_instruments`. The row is
    # still written, but nothing will price it — the UI has to say so.
    unpriced: bool = False


class ImportPreviewResponse(BaseModel):
    # PUL-95: the import is destructive in one direction (closed positions are
    # deleted), so the envelope carries every consequence next to the data rather
    # than leaving the caller to infer them from a bare position list.
    model_config = ConfigDict(extra="ignore")
    positions: list[ImportPositionOut] = []
    # Held today AND closed in the file — the exact list a commit would delete.
    closed: list[str] = []
    # Tickers the app cannot price. Saved anyway; see the endpoint's docstring.
    unknown_tickers: list[str] = []
    # Held today, absent from the file — left alone. S2B and the spin-offs to come
    # live here: a broker export structurally cannot contain them.
    untouched: list[str] = []
    dividends_new: int = 0
    # None means the export did not state a balance — deliberately distinct from
    # 0.0, which means the account really is empty.
    cash_pln: float | None = None
    warnings: list[str] = []


# openpyxl reads the whole workbook into memory and the app sets no body limit
# anywhere; the largest real export is well under a megabyte.
_IMPORT_MAX_BYTES = 5 * 1024 * 1024


def _resolve_import(user_id: str, portfolio_id: str, broker: str, data: bytes):
    """Parse an upload and resolve it against what the wallet holds today.

    Preview and commit share this entire path. The file is uploaded and parsed
    again on commit — a preview token cached in one process would be unknown to
    the other Cloud Run instance — so computing the disclosure in one place is
    the only thing guaranteeing the user confirmed what actually executes.

    Returns ``(preview, positions, closed)`` where ``positions`` is the payload
    for the bulk upsert and ``closed`` is the exact delete list.
    """
    if portfolio_id == _ALL_PORTFOLIOS:
        # No write path resolves the aggregate sentinel; the ownership check below
        # would reject it only by accident, and silently.
        raise HTTPException(status_code=422, detail="Nie mozna importowac do widoku zbiorczego")
    if len(data) > _IMPORT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Plik przekracza 5 MB")

    try:
        wallets = list_user_portfolios(user_id)
    except BigQueryError as exc:
        logger.error("BQ error listing wallets during import: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    if not any(w["portfolio_id"] == portfolio_id for w in wallets):
        raise HTTPException(status_code=404, detail="Wallet not found")

    try:
        parsed = get_parser(broker)(data)
    except BrokerImportError as exc:
        # Structural failure only: unreadable file, unknown broker, missing sheet.
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        known = set(list_distinct_portfolio_tickers())
        held = {row["ticker"] for row in list_user_portfolio_positions(user_id, portfolio_id)}
    except BigQueryError as exc:
        logger.error("BQ error resolving import against the wallet: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    in_file = {p.ticker for p in parsed.positions}
    # The parser reports every ticker that ever went to zero — two dozen on a real
    # file. Announcing the deletion of positions the user does not hold is a false
    # destructive warning, so only the intersection is disclosed and deleted.
    closed = sorted(t for t in set(parsed.closed_tickers) if t in held)
    unknown = sorted(t for t in in_file if t not in known)
    preview = ImportPreviewResponse(
        positions=[
            ImportPositionOut(
                ticker=p.ticker,
                company_name=p.company_name,
                shares=p.shares,
                avg_buy_price=p.avg_buy_price,
                unpriced=p.ticker not in known,
            )
            for p in parsed.positions
        ],
        closed=closed,
        unknown_tickers=unknown,
        untouched=sorted(held - in_file - set(closed)),
        # Events present in the file. The merge is insert-only, so on a re-import
        # none of them land — this is an upper bound, not a delta.
        dividends_new=len(parsed.dividends),
        cash_pln=getattr(parsed, "cash_pln", None),
        warnings=list(parsed.warnings),
    )
    positions = [
        {
            "ticker": p.ticker,
            "company_name": p.company_name,
            "shares": p.shares,
            "avg_buy_price": p.avg_buy_price,
        }
        for p in parsed.positions
    ]
    cash = _cash_position(getattr(parsed, "cash_pln", None))
    if cash is not None:
        positions.append(cash)
    return preview, positions, closed, parsed


def _cash_position(cash_pln: float | None) -> dict | None:
    """Uninvested cash as an ordinary position, or None when the file did not say.

    Stored as a position rather than a column on the wallet so the table, the
    treemap, the calendar and the value chart all count it through the path they
    already use. Priced at 1.00 PLN, so shares carry the balance.

    Only an *unstated* balance is skipped. A stated one is always emitted, even
    at zero: the bulk merge deliberately has no "delete what is absent from the
    source" branch — that is what keeps a holding like S2B, which the export
    cannot see, from being wiped. The same property means an omitted `_CASH` row
    is not cleared but *left behind*, so skipping a zero balance let spent cash
    sit in the wallet forever, inflating its value on every surface. A balance
    the export did not state and a balance it stated as zero are two different
    facts and must not take the same path.

    A negative balance (an account mid-settlement) is clamped to zero rather
    than stored: as a position it would read as a short and subtract from the
    portfolio's value.
    """
    if cash_pln is None:
        return None
    return {
        "ticker": CASH_TICKER,
        "company_name": "Wolne środki",
        "shares": max(0.0, round(float(cash_pln), 2)),
        "avg_buy_price": 1.0,
    }


def _operation_rows(user_id: str, portfolio_id: str, broker: str, parsed, source_file: str) -> list[dict]:
    """Flatten parsed operations into storage rows.

    ``external_id`` is namespaced by wallet: the broker's own id is unique only
    within one account, and the MERGE keys on that column alone, so a bare id
    would let one user's import silently swallow another's row.
    """
    imported_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for index, op in enumerate(parsed.operations):
        raw_id = op.external_id or f"idx{index}"
        rows.append({
            "user_id": user_id,
            "portfolio_id": portfolio_id,
            "broker": broker,
            "external_id": f"{user_id}:{portfolio_id}:{broker}:{raw_id}",
            "op_type": op.op_type,
            "occurred_at": op.occurred_at.isoformat(),
            "imported_at": imported_at,
            "amount_pln": op.amount_pln,
            "raw_type": op.raw_type,
            "ticker": normalize_ticker(op.ticker) if op.ticker else None,
            "instrument_name": op.instrument_name,
            "volume": op.volume,
            "unit_price": op.unit_price,
            "comment": op.comment,
            "source_file": source_file,
        })
    return rows


class AnnouncementUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: str | None = None
    ticker: str | None = None
    event_type: str | None = None
    structured_analysis: dict | None = None
    published_at: datetime | None = None


class PublicAnnouncement(BaseModel):
    # Public landing-card contract (PUL-72): no analysis_score, no sentiment,
    # no raw structured_analysis — only the parsed summary_pl survives as `summary`.
    model_config = ConfigDict(extra="ignore")
    company: str | None = None
    ticker: str | None = None
    title: str | None = None
    event_type: str | None = None
    published_at: datetime | None = None
    summary: str | None = None


class PortfolioWalletCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    portfolio_type: Literal["glowny", "ikze", "ike", "ppk", "ppe", "inny"]
    portfolio_name: str | None = None


class PortfolioPositionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    portfolio_id: str
    ticker: str
    company_name: str
    shares: float
    avg_buy_price: float


class NotificationSettingsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool


class PortfolioPositionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticker: str
    company_name: str | None
    shares: float
    avg_buy_price: float
    current_price: float | None = None
    daily_change_pct: float | None = None
    # The session's move in PLN per share. Shipped alongside the percentage so the
    # summary tile can sum shares x this number — the same arithmetic the calendar
    # does — instead of re-deriving it from the percentage and landing on a
    # different total for the same day.
    daily_change_per_share: float | None = None
    pnl_pln: float | None = None
    pnl_pct: float | None = None
    price_as_of: str | None = None
    price_history: list[float] | None = None


# PUL-90: sentinel portfolio_id selecting the "Wszystkie" (all-portfolios) aggregate
# view. Cannot collide with real (UUID-like) portfolio ids.
_ALL_PORTFOLIOS = "all"

_POSITION_MARKET_FIELDS = (
    "current_price",
    "daily_change_pct",
    "daily_change_per_share",
    "price_as_of",
    "price_history",
)


def _merge_positions_by_ticker(rows: list[dict]) -> list[dict]:
    """Merge positions across portfolios into one row per ticker (the "Wszystkie" view).

    Sums shares and computes a weighted-average avg_buy_price. The market-data bundle
    (current_price, daily_change_pct, price_as_of, price_history) is carried from the
    row with the freshest price_as_of — rows share a per-ticker price scan so this is a
    tie today, but preferring the newest avoids stale data if a source ever diverges.
    company_name takes the first non-null. P&L is left to the caller, recomputed from
    the merged shares / prices.
    """
    merged: dict[str, dict] = {}
    for row in rows:
        ticker = row.get("ticker")
        shares = row.get("shares") or 0.0
        avg_buy_price = row.get("avg_buy_price") or 0.0
        acc = merged.get(ticker)
        if acc is None:
            acc = {"ticker": ticker, "company_name": None, "shares": 0.0, "_cost": 0.0}
            acc.update({k: None for k in _POSITION_MARKET_FIELDS})
            merged[ticker] = acc
        acc["shares"] += shares
        acc["_cost"] += shares * avg_buy_price
        if acc["company_name"] is None and row.get("company_name") is not None:
            acc["company_name"] = row.get("company_name")
        # Carry the market-data bundle from the row with the freshest price_as_of.
        row_as_of = row.get("price_as_of")
        if row.get("current_price") is not None and (
            acc["price_as_of"] is None
            or (row_as_of is not None and row_as_of > acc["price_as_of"])
        ):
            for k in _POSITION_MARKET_FIELDS:
                acc[k] = row.get(k)
    out: list[dict] = []
    for acc in merged.values():
        shares = acc["shares"]
        acc["avg_buy_price"] = acc.pop("_cost") / shares if shares else 0.0
        out.append(acc)
    return out


def create_app() -> FastAPI:
    ui_html = pathlib.Path("static/index.html").read_text(encoding="utf-8")

    app = FastAPI()
    app.include_router(auth_router)

    @app.middleware("http")
    async def _add_process_time_header(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        elapsed_ms = (time.time() - start) * 1000
        response.headers["X-Process-Time"] = f"{elapsed_ms:.1f}ms"
        return response

    @app.on_event("startup")
    async def _init_dimension_tables():
        create_watchlist_table_if_not_exists()
        ensure_watchlist_schema_current()
        create_companies_table_if_not_exists()
        ensure_companies_schema_current()
        create_user_portfolio_positions_table_if_not_exists()
        ensure_user_portfolio_positions_schema_current()
        create_user_portfolios_table_if_not_exists()
        ensure_user_portfolios_schema_current()
        create_users_table_if_not_exists()
        ensure_users_schema_current()
        create_notification_subscriptions_table_if_not_exists()
        ensure_notification_subscriptions_schema_current()
        # Order matters: ensure_schema_current is a silent no-op on a table that
        # does not exist yet, so create_* must come first.
        create_user_broker_operations_table_if_not_exists()
        ensure_user_broker_operations_schema_current()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def ui():
        return ui_html

    @app.get("/auth/role")
    async def auth_role(role: Role = Depends(_get_role)):
        return {"role": role}

    @app.get("/api/public/top-announcements")
    async def public_top_announcements():
        # Public route by design (PUL-72): no _get_role dependency. The 60s
        # cache bounds BQ load regardless of landing-page traffic.
        cached = _perf_get("public:top-announcements", ttl=60)
        if cached is not None:
            return cached
        try:
            rows = list_top_announcements_public()
        except BigQueryError as exc:
            logger.error("BQ error in /api/public/top-announcements: %s", exc)
            # Negative cache: a BQ outage on this unauthenticated surface must
            # not let every request fire a fresh query — serve an empty card
            # list (the landing hides the strip) and retry after the TTL.
            _perf_set("public:top-announcements", [])
            return []
        result = []
        for r in rows:
            structured_analysis = _parse_structured_analysis(r.get("structured_analysis"))
            summary = structured_analysis.get("summary_pl") if structured_analysis else None
            result.append(
                PublicAnnouncement(
                    company=r.get("company"),
                    ticker=r.get("ticker"),
                    title=r.get("title"),
                    event_type=r.get("event_type"),
                    published_at=r.get("published_at"),
                    summary=summary,
                ).model_dump()
            )
        _perf_set("public:top-announcements", result)
        return result

    @app.get("/announcements")
    async def announcements(
        request: Request,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        ticker: str | None = None,
        company: str | None = None,
        event_type: str | None = None,
        from_dt: datetime | None = Query(None, alias="from"),
        to_dt: datetime | None = Query(None, alias="to"),
        role: Role = Depends(_get_role),
    ):
        if "limit" in request.query_params:
            raise HTTPException(status_code=422, detail="'limit' is removed; use 'page' and 'page_size'")
        try:
            if role == "admin":
                rows = list_announcements_admin(
                    page=page, page_size=page_size, ticker=ticker, company=company,
                    event_type=event_type, from_dt=from_dt, to_dt=to_dt,
                )
                return [
                    AnnouncementAdmin(
                        **{**r, "structured_analysis": _parse_structured_analysis(r.get("structured_analysis"))}
                    ).model_dump()
                    for r in rows
                ]
            else:
                rows = list_announcements_user(
                    page=page, page_size=page_size, ticker=ticker, company=company,
                    event_type=event_type, from_dt=from_dt, to_dt=to_dt,
                )
                result = []
                for r in rows:
                    structured_analysis = _parse_structured_analysis(r.get("structured_analysis"))
                    if structured_analysis is not None:
                        structured_analysis.pop("sentiment", None)
                    result.append(
                        AnnouncementUser(**{**r, "structured_analysis": structured_analysis}).model_dump()
                    )
                return result
        except BigQueryError as exc:
            logger.error("BQ error in /announcements: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/autocomplete/tickers")
    async def autocomplete_tickers(role: Role = Depends(_get_role)) -> list[str]:
        cached = _ac_get("tickers")
        if cached is not None:
            return cached
        try:
            data = list_distinct_tickers()
        except BigQueryError as exc:
            logger.error("BQ error in /autocomplete/tickers: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _ac_set("tickers", data)
        return data

    @app.get("/autocomplete/companies")
    async def autocomplete_companies(role: Role = Depends(_get_role)) -> list[str]:
        cached = _ac_get("companies")
        if cached is not None:
            return cached
        try:
            data = list_distinct_companies()
        except BigQueryError as exc:
            logger.error("BQ error in /autocomplete/companies: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _ac_set("companies", data)
        return data

    @app.get("/autocomplete/etf-instruments")
    async def autocomplete_etf_instruments(role: Role = Depends(_get_role)) -> dict:
        cached = _ac_get("etf-instruments")
        if cached is not None:
            return {"instruments": cached}
        try:
            data = list_etf_instruments_for_autocomplete()
        except BigQueryError as exc:
            logger.error("BQ error in /autocomplete/etf-instruments: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _ac_set("etf-instruments", data)
        return {"instruments": data}

    @app.get("/watchlist")
    async def get_watchlist(
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            tickers = list_watchlist_tickers(user_id)
        except BigQueryError as exc:
            logger.error("BQ error in GET /watchlist: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        return {"tickers": tickers}

    @app.post("/watchlist/{ticker}")
    async def post_watchlist(
        ticker: str,
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            known_tickers = list_distinct_tickers()
            if ticker not in known_tickers:
                raise HTTPException(status_code=422, detail="Unknown ticker")
            add_watchlist_ticker(user_id, ticker)
        except BigQueryError as exc:
            logger.error("BQ error in POST /watchlist/%s: %s", ticker, exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _invalidate_wl_sentiment(user_id)
        return {"ticker": ticker, "added": True}

    @app.delete("/watchlist/{ticker}", status_code=204)
    async def delete_watchlist(
        ticker: str,
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            remove_watchlist_ticker(user_id, ticker)
        except BigQueryError as exc:
            logger.error("BQ error in DELETE /watchlist/%s: %s", ticker, exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _invalidate_wl_sentiment(user_id)

    @app.get("/api/notifications/settings")
    async def get_notifications_settings(user_id: str = Depends(_get_user_id)):
        try:
            return get_notification_settings(user_id)
        except BigQueryError as exc:
            logger.error("BQ error in GET /api/notifications/settings: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/notifications/settings")
    async def post_notifications_settings(
        body: NotificationSettingsIn,
        user_id: str = Depends(_get_user_id),
        email: str | None = Depends(_get_user_email),
    ):
        # Address is the verified account email from the JWT — never the body.
        try:
            upsert_notification_settings(user_id, email, enabled=body.enabled)
            return get_notification_settings(user_id)
        except BigQueryError as exc:
            logger.error("BQ error in POST /api/notifications/settings: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/announcements/my-wallet")
    async def announcements_my_wallet(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        from_dt: datetime | None = Query(None, alias="from"),
        to_dt: datetime | None = Query(None, alias="to"),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            rows = list_announcements_for_watchlist(
                user_id, page=page, page_size=page_size, from_dt=from_dt, to_dt=to_dt,
            )
            if role == "admin":
                return [
                    AnnouncementAdmin(
                        **{**r, "structured_analysis": _parse_structured_analysis(r.get("structured_analysis"))}
                    ).model_dump()
                    for r in rows
                ]
            result = []
            for r in rows:
                structured_analysis = _parse_structured_analysis(r.get("structured_analysis"))
                if structured_analysis is not None:
                    structured_analysis.pop("sentiment", None)
                result.append(
                    AnnouncementUser(**{**r, "structured_analysis": structured_analysis}).model_dump()
                )
            return result
        except BigQueryError as exc:
            logger.error("BQ error in /announcements/my-wallet: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/announcements/my-wallet/sentiment-summary")
    async def announcements_my_wallet_sentiment_summary(
        role: Role = Depends(_require_admin),
        user_id: str = Depends(_get_user_id),
    ):
        # Admin-only + per-user (PUL-87): sentiment/score never reach the user role,
        # so this is gated at the dependency, not via model stripping. Short-TTL
        # per-user cache mirrors the /admin/portfolio/treemap pattern.
        cache_key = f"wl-sentiment-sum:{user_id}"
        cached = _perf_get(cache_key, ttl=60)
        if cached is not None:
            return cached
        try:
            summary = summarize_watchlist_sentiment(user_id)
        except BigQueryError as exc:
            logger.error("BQ error in /announcements/my-wallet/sentiment-summary: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _perf_set(cache_key, summary)
        return summary

    @app.get("/announcements/my-wallet/sentiment/{bucket}")
    async def announcements_my_wallet_sentiment_list(
        bucket: str,
        role: Role = Depends(_require_admin),
        user_id: str = Depends(_get_user_id),
    ):
        # Admin-only + per-user drill-down (PUL-87): lists the announcements behind a
        # single bar bucket, sharing the summary's normalization so contents match the
        # bar count. Score/sentiment stay admin-gated at the dependency (not stripped).
        if bucket not in _SENTIMENT_BUCKETS:
            raise HTTPException(status_code=422, detail=f"invalid sentiment bucket: {bucket}")
        cache_key = f"wl-sentiment-list:{user_id}:{bucket}"
        cached = _perf_get(cache_key, ttl=60)
        if cached is not None:
            return cached
        try:
            rows = list_watchlist_by_sentiment(user_id, bucket, limit=_WL_SENTIMENT_LIST_CAP)
        except BigQueryError as exc:
            logger.error("BQ error in /announcements/my-wallet/sentiment/%s: %s", bucket, exc)
            raise HTTPException(status_code=500, detail=str(exc))
        items = [
            {**r, "structured_analysis": _parse_structured_analysis(r.get("structured_analysis"))}
            for r in rows
        ]
        result = {"items": items, "truncated": len(items) >= _WL_SENTIMENT_LIST_CAP}
        _perf_set(cache_key, result)
        return result

    @app.get("/admin/x-posts")
    async def admin_x_posts(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        window: str | None = None,
        x_publish_status: str | None = None,
        post_text: str | None = None,
        from_dt: datetime | None = Query(None, alias="from"),
        to_dt: datetime | None = Query(None, alias="to"),
        role: Role = Depends(_require_admin),
    ):
        try:
            rows = list_x_posts_admin(
                page=page, page_size=page_size, window=window,
                x_publish_status=x_publish_status, post_text=post_text,
                from_dt=from_dt, to_dt=to_dt,
            )
            return [XPostAdmin(**r).model_dump() for r in rows]
        except BigQueryError as exc:
            logger.error("BQ error in /admin/x-posts: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/admin/portfolio/treemap")
    async def admin_portfolio_treemap(role: Role = Depends(_require_admin)):
        cached = _perf_get("admin:treemap", ttl=60)
        if cached is not None:
            return cached
        try:
            # Round 1 (parallel): fetch latest snapshot for each wallet
            main_snap, ikze_snap = await asyncio.gather(
                asyncio.to_thread(get_latest_snapshot_for_wallet, "main"),
                asyncio.to_thread(get_latest_snapshot_for_wallet, "ikze"),
            )
            snaps = {"main": main_snap, "ikze": ikze_snap}
            active = {w: s for w, s in snaps.items() if s is not None}
            result: dict[str, list[dict] | str | None] = {w: [] for w in _TREEMAP_WALLETS}
            if active:
                latest_date = max(s["snapshot_date"] for s in active.values())
                active_list = list(active.items())
                # Round 2 (parallel): fetch prior snapshots + stats_fetched_at
                gathered = await asyncio.gather(
                    *[asyncio.to_thread(get_latest_snapshot_before, w, s["snapshot_date"]) for w, s in active_list],
                    asyncio.to_thread(get_latest_company_stats_fetched_at, latest_date),
                )
                priors, stats_fetched_at = gathered[:-1], gathered[-1]
                for (wallet, latest), prior in zip(active_list, priors):
                    positions = compute_treemap_positions(
                        latest["positions_json"],
                        prior["positions_json"] if prior else None,
                        latest["total_value"],
                    )
                    result[wallet] = [TreemapPosition(**p).model_dump() for p in positions]
                result["as_of"] = latest_date.isoformat() if hasattr(latest_date, "isoformat") else str(latest_date)
                result["stats_fetched_at"] = stats_fetched_at
            else:
                result["as_of"] = None
                result["stats_fetched_at"] = None
            _perf_set("admin:treemap", result)
            return result
        except BigQueryError as exc:
            logger.error("BQ error in /admin/portfolio/treemap: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        except ValidationError as exc:
            logger.error("Malformed position data in /admin/portfolio/treemap: %s", exc)
            raise HTTPException(status_code=500, detail="Malformed position data")

    @app.delete("/announcements/{announcement_id}", status_code=204)
    async def delete(announcement_id: str, role: Role = Depends(_require_admin)):
        try:
            delete_announcement(announcement_id)
        except BigQueryError as exc:
            if "no row matched" in str(exc):
                raise HTTPException(status_code=404, detail="Not found")
            logger.error("BQ error in DELETE /announcements/%s: %s", announcement_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/portfolio/positions")
    async def get_portfolio_positions(
        portfolio_id: str = Query(...),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        cache_key = f"positions:{user_id}:{portfolio_id}"
        cached = _perf_get(cache_key, ttl=30)
        if cached is not None:
            return cached
        all_mode = portfolio_id == _ALL_PORTFOLIOS
        if not all_mode:
            try:
                wallets = list_user_portfolios(user_id)
            except BigQueryError as exc:
                logger.error("BQ error listing wallets in GET /api/portfolio/positions: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))
            if not any(w["portfolio_id"] == portfolio_id for w in wallets):
                raise HTTPException(status_code=404, detail="Wallet not found")
        try:
            rows = list_user_portfolio_positions(
                user_id, None if all_mode else portfolio_id, include_history=True
            )
        except BigQueryError as exc:
            logger.error("BQ error in GET /api/portfolio/positions: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        if all_mode:
            rows = _merge_positions_by_ticker(rows)
        result = []
        for row in rows:
            current_price = row.get("current_price")
            avg_buy_price = row.get("avg_buy_price")
            shares = row.get("shares")
            pnl_pln = (current_price - avg_buy_price) * shares if current_price is not None else None
            pnl_pct = (
                (current_price - avg_buy_price) / avg_buy_price * 100
                if current_price is not None and avg_buy_price
                else None
            )
            result.append(PortfolioPositionOut(**row, pnl_pln=pnl_pln, pnl_pct=pnl_pct).model_dump())
        _perf_set(cache_key, result)
        return result

    @app.post("/api/portfolio/positions")
    async def post_portfolio_position(
        body: PortfolioPositionIn,
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        if body.shares <= 0 or body.avg_buy_price <= 0:
            raise HTTPException(status_code=422, detail="shares and avg_buy_price must be > 0")
        try:
            wallets = list_user_portfolios(user_id)
        except BigQueryError as exc:
            logger.error("BQ error listing wallets in POST /api/portfolio/positions: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        if not any(w["portfolio_id"] == body.portfolio_id for w in wallets):
            raise HTTPException(status_code=404, detail="Wallet not found")
        try:
            known_tickers = list_distinct_portfolio_tickers()
            if body.ticker not in known_tickers:
                raise HTTPException(status_code=422, detail="Unknown ticker")
            upsert_user_portfolio_position(
                user_id, body.portfolio_id, body.ticker, body.company_name, body.shares, body.avg_buy_price
            )
            _perf_invalidate_portfolio(user_id, body.portfolio_id)
        except BigQueryError as exc:
            logger.error("BQ error in POST /api/portfolio/positions: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        return {"ticker": body.ticker, "upserted": True}

    @app.delete("/api/portfolio/positions/{ticker}", status_code=204)
    async def delete_portfolio_position(
        ticker: str,
        portfolio_id: str = Query(...),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            wallets = list_user_portfolios(user_id)
        except BigQueryError as exc:
            logger.error("BQ error listing wallets in DELETE /api/portfolio/positions/%s: %s", ticker, exc)
            raise HTTPException(status_code=500, detail=str(exc))
        if not any(w["portfolio_id"] == portfolio_id for w in wallets):
            raise HTTPException(status_code=404, detail="Wallet not found")
        try:
            delete_user_portfolio_position(user_id, portfolio_id, ticker)
            _perf_invalidate_portfolio(user_id, portfolio_id)
        except BigQueryError as exc:
            logger.error("BQ error in DELETE /api/portfolio/positions/%s: %s", ticker, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/portfolio/wallets")
    async def get_portfolio_wallets(
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            return list_user_portfolios(user_id)
        except BigQueryError as exc:
            logger.error("BQ error in GET /api/portfolio/wallets: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/portfolio/wallets", status_code=201)
    async def post_portfolio_wallet(
        body: PortfolioWalletCreate,
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            existing = list_user_portfolios(user_id)
        except BigQueryError as exc:
            logger.error("BQ error listing wallets in POST /api/portfolio/wallets: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        if body.portfolio_type in {"glowny", "ikze", "ike", "ppk", "ppe"}:
            if any(w["portfolio_type"] == body.portfolio_type for w in existing):
                raise HTTPException(status_code=409, detail="Wallet type already exists")
        elif body.portfolio_type == "inny":
            if sum(1 for w in existing if w["portfolio_type"] == "inny") >= 2:
                raise HTTPException(status_code=409, detail="Maximum 2 'Inny' wallets allowed")
        try:
            portfolio_id = create_user_portfolio(user_id, body.portfolio_type, body.portfolio_name)
            if body.portfolio_type == "glowny":
                assign_orphan_positions_to_portfolio(user_id, portfolio_id)
        except BigQueryError as exc:
            err = str(exc)
            if "Wallet type already exists" in err:
                raise HTTPException(status_code=409, detail="Wallet type already exists")
            if "Maximum 2" in err:
                raise HTTPException(status_code=409, detail="Maximum 2 'Inny' wallets allowed")
            logger.error("BQ error in POST /api/portfolio/wallets: %s", exc)
            raise HTTPException(status_code=500, detail=err)
        return {"portfolio_id": portfolio_id, "portfolio_type": body.portfolio_type, "portfolio_name": body.portfolio_name}

    @app.delete("/api/portfolio/wallets/{portfolio_id}", status_code=204)
    async def delete_portfolio_wallet(
        portfolio_id: str,
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        try:
            existing = list_user_portfolios(user_id)
        except BigQueryError as exc:
            logger.error("BQ error listing wallets in DELETE /api/portfolio/wallets/%s: %s", portfolio_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))
        if not any(w["portfolio_id"] == portfolio_id for w in existing):
            raise HTTPException(status_code=404, detail="Wallet not found")
        try:
            delete_user_portfolio(user_id, portfolio_id)
        except BigQueryError as exc:
            logger.error("BQ error in DELETE /api/portfolio/wallets/%s: %s", portfolio_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))
        # Without this the "Wszystkie" aggregate, the calendar, the chart and the
        # treemap keep serving the deleted wallet's holdings for the rest of the
        # cache window — the refetch right after the delete lands inside it.
        _perf_invalidate_portfolio(user_id, portfolio_id)

    @app.get("/api/portfolio/treemap")
    async def get_portfolio_treemap(
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        cache_key = f"treemap:{user_id}"
        cached = _perf_get(cache_key, ttl=60)
        if cached is not None:
            return cached
        try:
            wallets = list_user_portfolios(user_id)
        except BigQueryError as exc:
            logger.error("BQ error listing wallets in GET /api/portfolio/treemap: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        if not wallets:
            # Same keys as every other return from this endpoint — a response that
            # is a different shape depending on how much data exists is a trap for
            # whatever reads it next.
            return {"portfolios": [], "as_of": None, "stats_fetched_at": None}
        try:
            all_rows = list_user_portfolio_positions(user_id)
        except BigQueryError as exc:
            logger.error("BQ error fetching positions in GET /api/portfolio/treemap: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        rows_by_portfolio: dict[str, list[dict]] = {}
        for row in all_rows:
            rows_by_portfolio.setdefault(row.get("portfolio_id") or "", []).append(row)
        portfolios = []
        price_as_of_values: list[str] = []
        for wallet in wallets:
            pid = wallet["portfolio_id"]
            rows = rows_by_portfolio.get(pid, [])
            for row in rows:
                if row.get("price_as_of") is not None:
                    price_as_of_values.append(str(row["price_as_of"]))
            computed = compute_user_portfolio_treemap_positions(rows)
            positions = [TreemapPosition(**p).model_dump() for p in computed]
            portfolios.append({
                "portfolio_id": pid,
                "portfolio_type": wallet["portfolio_type"],
                "portfolio_name": wallet.get("portfolio_name"),
                "positions": positions,
            })
        as_of = max(price_as_of_values) if price_as_of_values else None
        stats_fetched_at: str | None = None
        if as_of:
            try:
                from datetime import date as _date
                as_of_date = _date.fromisoformat(as_of)
                stats_fetched_at = get_latest_company_stats_fetched_at(as_of_date)
            except Exception as exc:
                # PUL-113: the header degrading to a bare date is a real reported
                # symptom, and a silent `pass` left no way to tell a failed lookup
                # from a date that genuinely has no stats. Swallowing stays right —
                # a missing timestamp must not cost the user their treemap — but it
                # now leaves a trace.
                logger.warning(
                    "stats_fetched_at lookup failed for as_of=%s, header will show the date only: %s",
                    as_of,
                    exc,
                )
        response_data = {"portfolios": portfolios, "as_of": as_of, "stats_fetched_at": stats_fetched_at}
        _perf_set(cache_key, response_data)
        return response_data

    @app.get("/api/portfolio/calendar")
    async def get_portfolio_calendar(
        year: int,
        month: int,
        portfolio_id: str,
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        current_year = date.today().year
        if not (1 <= month <= 12):
            raise HTTPException(status_code=422, detail="month must be 1–12")
        # PUL-115: the floor used to be five years, which rejected half of what the
        # picker offered — the user chose 2019 and got "Błąd ładowania kalendarza".
        # A wallet's imported history reaches back as far as the broker's export
        # does, so the bound is a sanity guard, not a statement about the data.
        if not (current_year - _CALENDAR_MAX_YEARS_BACK <= year <= current_year + 1):
            raise HTTPException(
                status_code=422,
                detail=f"year must be in [{current_year - _CALENDAR_MAX_YEARS_BACK}, {current_year + 1}]",
            )
        cache_key = f"calendar:{user_id}:{portfolio_id}:{year}:{month}"
        cached = _perf_get(cache_key, ttl=300)
        if cached is not None:
            return cached
        all_mode = portfolio_id == _ALL_PORTFOLIOS
        if not all_mode:
            try:
                wallets = list_user_portfolios(user_id)
            except BigQueryError as exc:
                logger.error("BQ error listing wallets in GET /api/portfolio/calendar: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))
            if not any(w["portfolio_id"] == portfolio_id for w in wallets):
                raise HTTPException(status_code=403, detail="Portfolio not found or access denied")
        try:
            rows = get_portfolio_calendar_data(None if all_mode else portfolio_id, user_id, year, month)
        except BigQueryError as exc:
            logger.error("BQ error in GET /api/portfolio/calendar: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        # The picker's lower bound. A failure here must not cost the user the
        # calendar itself — without it the picker just falls back to its old range.
        try:
            inception = get_portfolio_inception(None if all_mode else portfolio_id, user_id)
        except BigQueryError as exc:
            logger.warning("inception lookup failed for portfolio=%s, picker falls back: %s", portfolio_id, exc)
            inception = None
        cal = compute_calendar_pnl(rows, year, month)
        result = PortfolioCalendarResponse(
            **cal, inception=inception.isoformat() if inception else None
        ).model_dump()
        _perf_set(cache_key, result)
        return result

    @app.get("/api/portfolio/history")
    async def get_portfolio_value_history(
        portfolio_id: str = Query(...),
        range: str = Query(...),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        start_date = _history_start_date(range)
        if start_date is None:
            raise HTTPException(status_code=422, detail="range must be one of 1w|1m|3m|1y")
        cache_key = f"history:{user_id}:{portfolio_id}:{range}"
        cached = _perf_get(cache_key, ttl=300)
        if cached is not None:
            return cached
        all_mode = portfolio_id == _ALL_PORTFOLIOS
        if not all_mode:
            try:
                wallets = list_user_portfolios(user_id)
            except BigQueryError as exc:
                logger.error("BQ error listing wallets in GET /api/portfolio/history: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))
            if not any(w["portfolio_id"] == portfolio_id for w in wallets):
                raise HTTPException(status_code=403, detail="Portfolio not found or access denied")
        try:
            data = get_portfolio_history(None if all_mode else portfolio_id, user_id, start_date)
        except BigQueryError as exc:
            logger.error("BQ error in GET /api/portfolio/history: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        result = PortfolioHistoryResponse(
            series=[
                PortfolioHistoryPoint(
                    date=row["snapshot_date"].isoformat(),
                    value_pln=row["value_pln"],
                    pnl_pln=row["pnl_pln"],
                )
                for row in data["series"]
            ],
            notes=[
                PortfolioHistoryNote(
                    ticker=note["ticker"],
                    listed_from=note["listed_from"].isoformat(),
                    price=note["price"],
                )
                for note in data["notes"]
            ],
            excluded=list(data["excluded"]),
            data_from=data["data_from"].isoformat() if data.get("data_from") else None,
        ).model_dump()
        _perf_set(cache_key, result)
        return result

    @app.post("/api/portfolio/import/preview")
    async def post_portfolio_import_preview(
        file: UploadFile = File(...),
        broker: str = Form(...),
        portfolio_id: str = Form(...),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        """Dry run: parse the export and disclose every consequence, writing nothing.

        An unrecognized ticker is reported, never a 422 — the user cannot edit the
        broker's file, and rejecting the whole upload over one instrument would
        leave them with no way in at all.
        """
        preview, _, _, _ = _resolve_import(user_id, portfolio_id, broker, await file.read())
        return preview.model_dump()

    @app.post("/api/portfolio/import/commit")
    async def post_portfolio_import_commit(
        file: UploadFile = File(...),
        broker: str = Form(...),
        portfolio_id: str = Form(...),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        preview, positions, closed, parsed = _resolve_import(
            user_id, portfolio_id, broker, await file.read()
        )
        try:
            # Three logical steps, never a loop — the request budget is 60s and a
            # 571-row merge alone measures ~4s.
            operations = merge_user_broker_operations(
                _operation_rows(user_id, portfolio_id, broker, parsed, file.filename or "")
            )
            written = merge_user_portfolio_positions_bulk(user_id, portfolio_id, positions)
            removed = delete_user_portfolio_positions(user_id, portfolio_id, closed)
        except BigQueryError as exc:
            logger.error("BQ error in POST /api/portfolio/import/commit: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _perf_invalidate_portfolio(user_id, portfolio_id)
        return {
            "operations_new": operations,
            "positions_written": written,
            "positions_removed": removed,
            "unknown_tickers": preview.unknown_tickers,
        }

    @app.get("/api/portfolio/dividends")
    async def get_portfolio_dividends(
        portfolio_id: str = Query(...),
        year: str | None = Query(None),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        # Validated before the key is built: an unchecked value goes straight into
        # the cache key, so any string would carve out its own entry.
        parsed_year: int | None = None
        if year not in (None, "", "all"):
            if not year.isdigit():
                raise HTTPException(status_code=422, detail="year must be a four-digit year")
            parsed_year = int(year)
        cache_key = f"dividends:{user_id}:{portfolio_id}:{parsed_year or 'all'}"
        cached = _perf_get(cache_key, ttl=300)
        if cached is not None:
            return cached
        all_mode = portfolio_id == _ALL_PORTFOLIOS
        if not all_mode:
            try:
                wallets = list_user_portfolios(user_id)
            except BigQueryError as exc:
                logger.error("BQ error listing wallets in GET /api/portfolio/dividends: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))
            if not any(w["portfolio_id"] == portfolio_id for w in wallets):
                raise HTTPException(status_code=404, detail="Wallet not found")
        try:
            result = get_dividend_summary(user_id, None if all_mode else portfolio_id, parsed_year)
        except BigQueryError as exc:
            logger.error("BQ error in GET /api/portfolio/dividends: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        _perf_set(cache_key, result)
        return result

    @app.get("/api/portfolio/realized")
    async def get_portfolio_realized(
        portfolio_id: str = Query(...),
        year: str | None = Query(None),
        role: Role = Depends(_get_role),
        user_id: str = Depends(_get_user_id),
    ):
        """Realized profit/loss on what has been sold, matched FIFO.

        Separate from the dividend totals on purpose: a dividend is cash the
        company paid, a realized result is what a sale returned against what the
        shares cost. One combined number would hide which of the two the
        portfolio is actually living on.
        """
        # Validated before the key is built, like the dividends endpoint: an
        # unchecked value goes straight into the cache key and carves out an entry.
        parsed_year: int | None = None
        if year not in (None, "", "all"):
            if not year.isdigit():
                raise HTTPException(status_code=422, detail="year must be a four-digit year")
            parsed_year = int(year)
        cache_key = f"realized:{user_id}:{portfolio_id}:{parsed_year or 'all'}"
        cached = _perf_get(cache_key, ttl=300)
        if cached is not None:
            return cached
        all_mode = portfolio_id == _ALL_PORTFOLIOS
        if not all_mode:
            try:
                wallets = list_user_portfolios(user_id)
            except BigQueryError as exc:
                logger.error("BQ error listing wallets in GET /api/portfolio/realized: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc))
            if not any(w["portfolio_id"] == portfolio_id for w in wallets):
                raise HTTPException(status_code=404, detail="Wallet not found")
        try:
            trades = list_broker_trades(user_id, None if all_mode else portfolio_id)
        except BigQueryError as exc:
            logger.error("BQ error in GET /api/portfolio/realized: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        result = compute_realized_pnl(trades, parsed_year)
        _perf_set(cache_key, result)
        return result

    app.mount("/static", StaticFiles(directory="static"), name="static")

    return app
