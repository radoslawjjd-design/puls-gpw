import logging
import os
import pathlib
import time
from datetime import datetime
from typing import Literal

logger = logging.getLogger(__name__)

import json5
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict

from db.bigquery import BigQueryError  # type: ignore[attr-defined]
from db.bigquery import (
    delete_announcement,
    list_announcements_admin,
    list_announcements_user,
    list_distinct_companies,
    list_distinct_tickers,
)

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
                return [
                    AnnouncementUser(
                        **{**r, "structured_analysis": _parse_structured_analysis(r.get("structured_analysis"))}
                    ).model_dump()
                    for r in rows
                ]
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

    @app.delete("/announcements/{announcement_id}", status_code=204)
    async def delete(announcement_id: str, role: Role = Depends(_require_admin)):
        try:
            delete_announcement(announcement_id)
        except BigQueryError as exc:
            if "no row matched" in str(exc):
                raise HTTPException(status_code=404, detail="Not found")
            logger.error("BQ error in DELETE /announcements/%s: %s", announcement_id, exc)
            raise HTTPException(status_code=500, detail=str(exc))

    return app
