import logging
import os
import pathlib
import time
from datetime import date, datetime
from typing import Literal

logger = logging.getLogger(__name__)

import json5
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from db.bigquery import BigQueryError  # type: ignore[attr-defined]
from db.bigquery import (
    add_watchlist_ticker,
    create_companies_table_if_not_exists,
    create_watchlist_table_if_not_exists,
    delete_announcement,
    ensure_companies_schema_current,
    ensure_watchlist_schema_current,
    get_latest_snapshot_before,
    get_latest_snapshot_for_wallet,
    list_announcements_admin,
    list_announcements_for_watchlist,
    list_announcements_user,
    list_distinct_companies,
    list_distinct_tickers,
    list_watchlist_tickers,
    list_x_posts_admin,
    remove_watchlist_ticker,
)
from src.portfolio_treemap import compute_treemap_positions

_AC_CACHE: dict[str, tuple[list[str], float]] = {}
_AC_TTL = 300  # 5 minutes


def _ac_get(key: str) -> list[str] | None:
    if key in _AC_CACHE:
        data, ts = _AC_CACHE[key]
        if time.time() - ts < _AC_TTL:
            return data
    return None


def _ac_set(key: str, data: list[str]) -> None:
    _AC_CACHE[key] = (data, time.time())

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_CLIENT_ID_HEADER = APIKeyHeader(name="X-Client-Id", auto_error=False)
Role = Literal["admin", "user"]


def _get_role(key: str | None = Security(_API_KEY_HEADER)) -> Role:
    if key == os.environ.get("ADMIN_API_KEY"):
        return "admin"
    if key == os.environ.get("USER_API_KEY"):
        return "user"
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _require_admin(role: Role = Depends(_get_role)) -> Role:
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return role


def _get_client_id(client_id: str | None = Security(_CLIENT_ID_HEADER)) -> str:
    if not client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")
    return client_id


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
    position_value_pln: float
    daily_change_pln: float | None = None
    daily_change_pct: float | None = None
    portfolio_share_pct: float | None = None
    since_purchase_pct: float | None = None
    since_purchase_pln: float | None = None


_TREEMAP_WALLETS = ("main", "ikze")


class AnnouncementUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    company: str | None = None
    ticker: str | None = None
    event_type: str | None = None
    structured_analysis: dict | None = None
    published_at: datetime | None = None


def create_app() -> FastAPI:
    ui_html = pathlib.Path("static/index.html").read_text(encoding="utf-8")

    app = FastAPI()

    @app.on_event("startup")
    async def _init_dimension_tables():
        create_watchlist_table_if_not_exists()
        ensure_watchlist_schema_current()
        create_companies_table_if_not_exists()
        ensure_companies_schema_current()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def ui():
        return ui_html

    @app.get("/auth/role")
    async def auth_role(role: Role = Depends(_get_role)):
        return {"role": role}

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

    @app.get("/watchlist")
    async def get_watchlist(
        role: Role = Depends(_get_role),
        client_id: str = Depends(_get_client_id),
    ):
        try:
            tickers = list_watchlist_tickers(client_id)
        except BigQueryError as exc:
            logger.error("BQ error in GET /watchlist: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
        return {"tickers": tickers}

    @app.post("/watchlist/{ticker}")
    async def post_watchlist(
        ticker: str,
        role: Role = Depends(_get_role),
        client_id: str = Depends(_get_client_id),
    ):
        try:
            known_tickers = list_distinct_tickers()
            if ticker not in known_tickers:
                raise HTTPException(status_code=422, detail="Unknown ticker")
            add_watchlist_ticker(client_id, ticker)
        except BigQueryError as exc:
            logger.error("BQ error in POST /watchlist/%s: %s", ticker, exc)
            raise HTTPException(status_code=500, detail=str(exc))
        return {"ticker": ticker, "added": True}

    @app.delete("/watchlist/{ticker}", status_code=204)
    async def delete_watchlist(
        ticker: str,
        role: Role = Depends(_get_role),
        client_id: str = Depends(_get_client_id),
    ):
        try:
            remove_watchlist_ticker(client_id, ticker)
        except BigQueryError as exc:
            logger.error("BQ error in DELETE /watchlist/%s: %s", ticker, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/announcements/my-wallet")
    async def announcements_my_wallet(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        from_dt: datetime | None = Query(None, alias="from"),
        to_dt: datetime | None = Query(None, alias="to"),
        role: Role = Depends(_get_role),
        client_id: str = Depends(_get_client_id),
    ):
        try:
            rows = list_announcements_for_watchlist(
                client_id, page=page, page_size=page_size, from_dt=from_dt, to_dt=to_dt,
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
            logger.error("BQ error in /announcements/my-wallet: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

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
        try:
            result: dict[str, list[dict] | str | None] = {}
            snapshot_dates: list[date] = []
            for wallet in _TREEMAP_WALLETS:
                latest = get_latest_snapshot_for_wallet(wallet)
                if latest is None:
                    result[wallet] = []
                    continue
                snapshot_dates.append(latest["snapshot_date"])
                prior = get_latest_snapshot_before(wallet, latest["snapshot_date"])
                positions = compute_treemap_positions(
                    latest["positions_json"],
                    prior["positions_json"] if prior else None,
                    latest["total_value"],
                )
                result[wallet] = [TreemapPosition(**p).model_dump() for p in positions]
            if snapshot_dates:
                latest_date = max(snapshot_dates)
                result["as_of"] = latest_date.isoformat() if hasattr(latest_date, "isoformat") else str(latest_date)
            else:
                result["as_of"] = None
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

    app.mount("/static", StaticFiles(directory="static"), name="static")

    return app
