"""
agents/cost_report_builder.py — weekly Vertex AI cost report data layer.

Łączy dwa źródła:
  1. BQ `billing_export.gcp_billing_export_v1_*` — autorytatywny rachunek
     (Total / dzień / SKU). Te liczby idą na fakturę Google.
  2. Langfuse `/api/public/observations` — atrybucja per-agent + top calls.
     Liczby z Langfuse derived z `usage_details × pricing constants` — nie
     muszą zgadzać się z BQ ±5% (Langfuse nie widzi cache storage fee).

Output: `CostReport` dataclass — gotowy do email rendering.

Cron: niedziela 19:00 Europe/Warsaw (po wszystkich weekendowych jobach,
przed pn intraday-premarket 08:50). Patrz `weekly_cost_report.py`.
"""
from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

logger = logging.getLogger(__name__)

# ── Stałe ──────────────────────────────────────────────────────────────────────

# Vertex AI billing service tag w gcp_billing_export.
_VERTEX_SERVICE = "Vertex AI"

# USD → PLN conversion (constants — fluktuacje kursowe ignorowane dla raportów).
_USD_TO_PLN = 4.05

# Anomaly threshold — flag SKU gdy daily max > mean_4w + N×stdev_4w.
_ANOMALY_SIGMA = 2.0

# Limit pojedynczych observations w get_many — pagination loop.
_LANGFUSE_PAGE_SIZE = 100

# Langfuse environment tag — domyślnie prod ("gwp"). Konfigurowalny.
_LANGFUSE_ENVIRONMENT_DEFAULT = "gwp"

# Tabela billing_export — zlokalizowana automatycznie via gcloud (widoczna w
# `bq ls billing_export`). Pełna nazwa wymaga znajomości billing account ID.
# Konfigurowane przez env var BILLING_EXPORT_TABLE.
_DEFAULT_BILLING_TABLE = (
    "oswiadczenia-gwp.billing_export.gcp_billing_export_v1_01DCC5_F9E552_E701D5"
)


# ── Dataclass ──────────────────────────────────────────────────────────────────


@dataclass
class CostReport:
    """Kompletny tygodniowy raport kosztów."""

    week_start: date
    week_end: date
    total_pln: float
    total_pln_prev_week: float
    per_sku: dict[str, float]                 # {"Thinking Text Output": 43.61, ...} PLN
    per_day: dict[date, float]                # {date(2026,4,21): 18.43, ...} PLN
    per_agent: dict[str, float]               # {"analysis": 28.5, "broker": 12.1, ...} PLN
    top_calls: list[dict]                     # [{id, trace_id, agent, cost_usd, cost_pln, model, start_time}]
    anomalies: list[dict]                     # [{sku, value, threshold, mean_4w, stdev_4w}]
    analyses_count: int
    cost_per_analysis_pln: float
    langfuse_total_pln: float = 0.0           # sanity check vs BQ total
    reconciliation_delta_pct: float = 0.0     # |BQ - Langfuse| / BQ × 100
    anomaly_threshold_pct: float = 30.0       # +30% week-over-week flag

    @property
    def delta_vs_prev_week_pct(self) -> float:
        if self.total_pln_prev_week == 0:
            return 0.0
        return (self.total_pln - self.total_pln_prev_week) / self.total_pln_prev_week * 100.0

    @property
    def has_weekly_anomaly(self) -> bool:
        return self.delta_vs_prev_week_pct > self.anomaly_threshold_pct

    @property
    def has_any_anomaly(self) -> bool:
        return self.has_weekly_anomaly or len(self.anomalies) > 0


# ── Builder orchestrator ───────────────────────────────────────────────────────


def build_weekly_cost_report(end_date: date) -> CostReport:
    """End-to-end report build dla tygodnia kończącego się w `end_date` (włącznie).

    Window: end_date - 6 → end_date (7 dni).
    Prev week: end_date - 13 → end_date - 7.
    Baseline 4w: end_date - 34 → end_date - 7 (4 tygodnie przed prev_week_end).
    """
    week_start = end_date - timedelta(days=6)
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = end_date - timedelta(days=7)
    baseline_start = prev_week_start - timedelta(days=21)  # 4 tygodnie wstecz
    baseline_end = prev_week_end

    bq = _get_bq_client()
    lf = _get_langfuse_client()

    # 1. BQ billing — this week + prev week + 4-week baseline
    this_week_costs = _fetch_vertex_costs_bq(bq, start=week_start, end=end_date)
    prev_week_costs = _fetch_vertex_costs_bq(bq, start=prev_week_start, end=prev_week_end)
    baseline_costs = _fetch_vertex_costs_bq(bq, start=baseline_start, end=baseline_end)

    # 2. BQ analyses count for cost-per-analysis
    analyses_count = _fetch_analyses_count_bq(bq, start=week_start, end=end_date)

    # 3. Aggregate this week
    total_usd = sum(
        cost for sku_dict in this_week_costs.values() for cost in sku_dict.values()
    )
    total_pln = total_usd * _USD_TO_PLN

    per_sku_pln: dict[str, float] = {}
    per_day_pln: dict[date, float] = {}
    for d, sku_dict in this_week_costs.items():
        per_day_pln[d] = sum(sku_dict.values()) * _USD_TO_PLN
        for sku, cost in sku_dict.items():
            per_sku_pln[sku] = per_sku_pln.get(sku, 0.0) + cost * _USD_TO_PLN

    # 4. Prev week total
    prev_total_usd = sum(
        cost for sku_dict in prev_week_costs.values() for cost in sku_dict.values()
    )
    prev_total_pln = prev_total_usd * _USD_TO_PLN

    # 5. Anomalies (per SKU vs 4w baseline)
    week_max_per_sku = _per_sku_daily_max(this_week_costs)
    baseline_stats = _per_sku_baseline_stats(baseline_costs)
    anomalies = _detect_anomalies(week_max_per_sku, baseline_stats)

    # 6. Langfuse — fetch ALL observations raz, potem split na top + per_agent.
    # Single fetch eliminuje duplikatowe wywołania API (latency + rate limits).
    start_dt = datetime.combine(week_start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
    all_lf_calls = _fetch_all_langfuse_calls(lf, start=start_dt, end=end_dt)

    top_calls_raw = sorted(all_lf_calls, key=lambda c: c["cost_usd"], reverse=True)[:5]
    per_agent_usd: dict[str, float] = {}
    for call in all_lf_calls:
        agent = call["agent"]
        per_agent_usd[agent] = per_agent_usd.get(agent, 0.0) + call["cost_usd"]

    per_agent_pln = {agent: cost * _USD_TO_PLN for agent, cost in per_agent_usd.items()}
    langfuse_total_pln = sum(per_agent_pln.values())

    # Wzbogać top_calls o cost_pln
    top_calls = [
        {**call, "cost_pln": call.get("cost_usd", 0.0) * _USD_TO_PLN}
        for call in top_calls_raw
    ]

    # 7. Reconciliation
    reconciliation_pct = (
        abs(total_pln - langfuse_total_pln) / total_pln * 100.0 if total_pln > 0 else 0.0
    )

    return CostReport(
        week_start=week_start,
        week_end=end_date,
        total_pln=round(total_pln, 2),
        total_pln_prev_week=round(prev_total_pln, 2),
        per_sku={k: round(v, 2) for k, v in per_sku_pln.items()},
        per_day={k: round(v, 2) for k, v in per_day_pln.items()},
        per_agent={k: round(v, 2) for k, v in per_agent_pln.items()},
        top_calls=top_calls,
        anomalies=anomalies,
        analyses_count=analyses_count,
        cost_per_analysis_pln=round(_cost_per_analysis(total_pln, analyses_count), 4),
        langfuse_total_pln=round(langfuse_total_pln, 2),
        reconciliation_delta_pct=round(reconciliation_pct, 1),
    )


# ── Data fetchers ──────────────────────────────────────────────────────────────


def _fetch_vertex_costs_bq(bq, start: date, end: date) -> dict[date, dict[str, float]]:
    """Query billing_export — Vertex AI cost USD per (day, sku)."""
    table = os.environ.get("BILLING_EXPORT_TABLE", _DEFAULT_BILLING_TABLE)
    query = f"""
    SELECT
      DATE(usage_start_time, 'Europe/Warsaw') AS day,
      sku.description AS sku,
      SUM(cost) AS cost_usd
    FROM `{table}`
    WHERE service.description = '{_VERTEX_SERVICE}'
      AND DATE(usage_start_time, 'Europe/Warsaw') BETWEEN '{start}' AND '{end}'
    GROUP BY day, sku
    HAVING cost_usd > 0
    """
    result: dict[date, dict[str, float]] = {}
    for row in bq._get_client().query(query).result():
        d = row["day"]
        sku = row["sku"]
        cost_usd = float(row["cost_usd"])
        result.setdefault(d, {})[sku] = result.get(d, {}).get(sku, 0.0) + cost_usd
    return result


def _fetch_analyses_count_bq(bq, start: date, end: date) -> int:
    """Count gwp.analyses w przedziale (cost-per-analysis denominator)."""
    dataset = os.environ.get("BQ_DATASET", "gwp")
    query = f"""
    SELECT COUNT(*) AS count
    FROM `{dataset}.analyses`
    WHERE analysis_date BETWEEN '{start}' AND '{end}'
    """
    try:
        for row in bq._get_client().query(query).result():
            return int(row["count"])
    except Exception as e:
        logger.warning(f"analyses count query failed: {e}")
    return 0


def _fetch_all_langfuse_calls(
    lf,
    start: datetime,
    end: datetime,
    environment: str | None = None,
) -> list[dict]:
    """Fetch wszystkich GENERATION observations w okienku — paginacja do empty page.

    Per-call pagination wygasza się gdy zwrócona strona ma 0 items
    (Langfuse API: ostatnia strona jest empty, NIE partial). Daje to deterministykę
    przy mockach (test musi zwrócić pustą stronę żeby loop się zakończył).

    Zwraca list[dict] z polami: id, trace_id, agent, model, cost_usd, start_time.
    """
    env = environment or _LANGFUSE_ENVIRONMENT_DEFAULT
    out: list[dict] = []
    page = 1
    while True:
        try:
            resp = lf.api.observations.get_many(
                from_start_time=start,
                to_start_time=end,
                type="GENERATION",
                limit=_LANGFUSE_PAGE_SIZE,
                page=page,
                environment=env,
            )
        except Exception as e:
            logger.warning(f"Langfuse observations.get_many page={page} failed: {e}")
            break
        items = list(getattr(resp, "data", []) or [])
        if not items:
            break
        for o in items:
            cost_usd = _extract_cost_usd(o)
            if cost_usd <= 0:
                continue
            out.append({
                "id": o.id,
                "trace_id": getattr(o, "trace_id", None),
                "agent": _extract_agent(o),
                "model": getattr(o, "model", None),
                "cost_usd": cost_usd,
                "start_time": getattr(o, "start_time", None),
            })
        page += 1
    return out


def _fetch_top_langfuse_calls(
    lf,
    start: datetime,
    end: datetime,
    limit: int = 5,
    environment: str | None = None,
) -> list[dict]:
    """Top N najdroższych observations (po cost_usd desc)."""
    all_calls = _fetch_all_langfuse_calls(lf, start, end, environment=environment)
    all_calls.sort(key=lambda c: c["cost_usd"], reverse=True)
    return all_calls[:limit]


def _fetch_per_agent_costs_langfuse(
    lf,
    start: datetime,
    end: datetime,
    environment: str | None = None,
) -> dict[str, float]:
    """Sum cost per metadata.agent (USD)."""
    all_calls = _fetch_all_langfuse_calls(lf, start, end, environment=environment)
    out: dict[str, float] = {}
    for call in all_calls:
        agent = call["agent"]
        out[agent] = out.get(agent, 0.0) + call["cost_usd"]
    return out


# ── Pure helpers (mockable / testowalne) ───────────────────────────────────────


def _extract_cost_usd(obs) -> float:
    """Wyciąga total cost USD z observation (preferuje cost_details.total)."""
    # Newer API: cost_details dict
    cd = getattr(obs, "cost_details", None) or {}
    if isinstance(cd, dict):
        if "total" in cd and cd["total"] is not None:
            try:
                return float(cd["total"])
            except (TypeError, ValueError):
                pass
    # Fallback: total_price / calculated_total_cost
    for attr in ("total_price", "calculated_total_cost"):
        v = getattr(obs, attr, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _extract_agent(obs) -> str:
    """Wyciąga `metadata.agent` (worker tagguje przy każdym call)."""
    meta = getattr(obs, "metadata", None) or {}
    if isinstance(meta, dict):
        return str(meta.get("agent") or "unknown")
    return "unknown"


def _per_sku_daily_max(
    per_day_per_sku: dict[date, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Dla każdego SKU policz {daily=avg, max=daily_max} w PLN."""
    by_sku: dict[str, list[float]] = {}
    for _d, sku_dict in per_day_per_sku.items():
        for sku, cost_usd in sku_dict.items():
            by_sku.setdefault(sku, []).append(cost_usd * _USD_TO_PLN)
    return {
        sku: {"daily": sum(vals) / len(vals), "max": max(vals)}
        for sku, vals in by_sku.items()
        if vals
    }


def _per_sku_baseline_stats(
    per_day_per_sku: dict[date, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Dla każdego SKU policz baseline {mean, stdev} z N dni (PLN)."""
    by_sku: dict[str, list[float]] = {}
    for _d, sku_dict in per_day_per_sku.items():
        for sku, cost_usd in sku_dict.items():
            by_sku.setdefault(sku, []).append(cost_usd * _USD_TO_PLN)
    out: dict[str, dict[str, float]] = {}
    for sku, vals in by_sku.items():
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        stdev = statistics.stdev(vals) if len(vals) >= 2 else 0.0
        out[sku] = {"mean": mean, "stdev": stdev}
    return out


def _detect_anomalies(
    week_per_sku: dict[str, dict[str, float]],
    baseline_per_sku: dict[str, dict[str, float]],
) -> list[dict]:
    """Flag per-SKU spike: daily_max > baseline.mean + N×baseline.stdev."""
    anomalies: list[dict] = []
    for sku, week_stats in week_per_sku.items():
        baseline = baseline_per_sku.get(sku)
        if not baseline:
            continue
        stdev = baseline.get("stdev", 0.0)
        if stdev <= 0:
            continue  # nie flaguj dla constant baseline (szum / zero variance)
        threshold = baseline["mean"] + _ANOMALY_SIGMA * stdev
        max_value = week_stats.get("max", 0.0)
        if max_value > threshold:
            anomalies.append({
                "sku": sku,
                "value": round(max_value, 2),
                "threshold": round(threshold, 2),
                "mean_4w": round(baseline["mean"], 2),
                "stdev_4w": round(stdev, 2),
            })
    return anomalies


def _cost_per_analysis(total_pln: float, analyses_count: int) -> float:
    """PLN/analiza — bezpieczne dla zero analyses."""
    if analyses_count <= 0:
        return 0.0
    return total_pln / analyses_count


# ── Lazy clients (mockowalne w testach via monkeypatch) ────────────────────────


def _get_bq_client():
    from storage.bq_client import get_bq_client
    return get_bq_client()


def _get_langfuse_client():
    """Singleton z vertex_client (już skonfigurowany)."""
    from agents.vertex_client import _get_langfuse_client as _vc_get
    client = _vc_get()
    if client is None:
        raise RuntimeError(
            "Langfuse client unavailable — sprawdź LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST"
        )
    return client
