"""
Generator: thread X z decyzjami eksperymentalnych portfeli AI (F7.2).

Flow F7.2:
- NIEDZIELA 13:00 — broker.py generuje raport + email do user (decyzje + draft)
- PONIEDZIAŁEK 10:30 — xpost --window broker_decisions auto-publish na X
  (po sesji od 9:00 user już zrealizował transakcje wg AI)

Compliance constraints:
- ❌ ZAKAZ "kupuj/sprzedaj/warto/rekomend/polec" (validator hard-fails)
- ✅ PAST tense ("Żółw KUPIŁ", "Zając SPRZEDAŁ")
- ✅ Framing "eksperymentalne portfele AI"
- ✅ Strategy A — wymagane kody GPW w prompcie
"""
from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime

from agents.xpost.base import _SYSTEM, _call_gemini
from agents.xpost.formatters import _extract_tweet
from agents.xpost.templates import _BROKER_DECISIONS_TEMPLATE

logger = logging.getLogger(__name__)


def _format_decision(d: dict, action: str, portfolio_label: str) -> str:
    """Pojedyncza decyzja w czytelnej formie dla promptu."""
    ticker = (d.get("ticker") or "").strip().upper()
    company = (d.get("company") or "").strip().upper()
    amount = d.get("amount_pln")
    conviction = d.get("conviction", "")
    reasoning = (d.get("reasoning") or "").strip()
    horizon = d.get("horizon", "")
    risks = d.get("risks", "")

    parts = [f"  [{portfolio_label}] {action} {company} (${ticker})"]
    if amount:
        parts.append(f"    kwota: {amount} PLN, conviction: {conviction}")
    if reasoning:
        parts.append(f"    powód: {reasoning}")
    if horizon:
        parts.append(f"    horyzont: {horizon}")
    if risks:
        parts.append(f"    ryzyka: {risks}")
    return "\n".join(parts)


def _build_decisions_section(report: dict, label: str) -> tuple[str, list[dict]]:
    """Buduje czytelną sekcję decyzji + zwraca listę spółek (do Strategy A)."""
    decisions = []
    companies = []

    # SPRZEDAJ z portfolio_evaluations
    for ev in report.get("portfolio_evaluations") or []:
        if (ev.get("recommendation") or "").upper() == "SPRZEDAJ":
            decisions.append(_format_decision(
                ev, action="SPRZEDAŁ", portfolio_label=label,
            ))
            companies.append(ev)

    # KUP z buy_recommendations + short_term_recommendations
    for rec in (report.get("buy_recommendations") or []) + (report.get("short_term_recommendations") or []):
        decisions.append(_format_decision(
            rec, action="KUPIŁ", portfolio_label=label,
        ))
        companies.append(rec)

    if not decisions:
        return "  (brak decyzji w tym portfelu)", []
    return "\n\n".join(decisions), companies


def _build_required_block(companies: list[dict]) -> str:
    """Strategy A — mapping nazwa→kod GPW dla wszystkich decyzji."""
    from utils.gpw_tickers import get_gpw_tickers, name_to_ticker

    gpw_set = get_gpw_tickers()
    seen: set[str] = set()
    lines: list[str] = []

    for c in companies:
        ticker = (c.get("ticker") or "").strip().upper()
        company = (c.get("company") or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)

        # Preferuj `ticker` z broker_report (już jest kod GPW); fallback do mappingu.
        kod = name_to_ticker(company) if company else None
        chosen = ticker or kod
        if chosen in gpw_set:
            lines.append(f"  - {company or chosen} → ${chosen}")
        elif kod:
            lines.append(f"  - {company} → ${kod}")
        else:
            lines.append(f"  - {company or chosen} → ${chosen} (poza whitelist — fallback)")

    if not lines:
        return ""
    return (
        "\n=== WYMAGANE KODY GPW (UŻYJ DOKŁADNIE TYCH CASHTAGÓW) ===\n"
        + "\n".join(lines)
        + "\nKAŻDA decyzja MUSI mieć cashtag z TEJ LISTY (nie pomijaj $!).\n"
    )


def generate_xpost_broker_decisions(
    broker_report: dict,
    data: date,
    broker_short_report: dict | None = None,
) -> dict:
    """
    Generuje thread X z podsumowaniem decyzji eksperymentalnych portfeli AI.

    Args:
        broker_report:       wiersz z `broker_reports` BQ za miniony tydzień
                             (portfolio_evaluations + buy_recommendations + short_term).
        data:                data publikacji posta (zwykle pn po nd analizy).
        broker_short_report: opcjonalnie raport portfela krótkoterminowego (Short).

    Zwraca: {"is_thread": bool, "tweets": [str, ...]}.
    """
    today_str = datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
    system = _SYSTEM.format(today=today_str)

    week_from = broker_report.get("week_from") or broker_report.get("data_od")
    week_to = broker_report.get("week_to") or broker_report.get("data_do")
    market_sentiment = broker_report.get("market_sentiment") or broker_report.get("sentyment_rynku") or "neutralny"

    # ── Sekcje decyzji per portfel ──────────────────────────────────────────
    standard_section, std_companies = _build_decisions_section(broker_report, label="🐢 Standard")
    if broker_short_report:
        short_section, short_companies = _build_decisions_section(broker_short_report, label="🐇 Short")
    else:
        short_section, short_companies = ("  (brak danych)", [])

    all_companies = std_companies + short_companies
    required_block = _build_required_block(all_companies)

    if not all_companies:
        logger.info("broker_decisions: brak decyzji — skip publikacji.")
        return {"is_thread": False, "tweets": []}

    prompt = (
        f"{system}\n\n"
        + _BROKER_DECISIONS_TEMPLATE.format(
            data_post        = data.strftime("%d.%m.%Y"),
            week_from        = str(week_from) if week_from else "?",
            week_to          = str(week_to) if week_to else "?",
            market_sentiment = market_sentiment,
            decisions_standard = standard_section,
            decisions_short    = short_section,
            required_block     = required_block,
        )
    )

    # ── Cashtag v2 opt-in: append override suffix z nowymi regułami ─────────
    from config import XPOST_CASHTAG_V2_WINDOWS
    if "broker_decisions" in XPOST_CASHTAG_V2_WINDOWS:
        from agents.xpost.cashtag_rules import CASHTAG_V2_OVERRIDE_SUFFIX
        prompt += CASHTAG_V2_OVERRIDE_SUFFIX

    result = _call_gemini(prompt, metadata={
        "agent":           "xpost",
        "window":          "broker_decisions",
        "date":            str(data),
        "n_decisions":     len(all_companies),
        "target_thread_length": min(6, len(all_companies) + 2),
    })

    if result and "tweets" in result:
        result["tweets"] = [_extract_tweet(t) for t in result["tweets"]]
        result["is_thread"] = len(result["tweets"]) > 1
        logger.info(
            f"xpost broker_decisions wygenerowany: "
            f"is_thread={result.get('is_thread')}, "
            f"tweets={len(result['tweets'])}"
        )
        return result

    logger.warning("Gemini fallback dla broker_decisions")
    fallback = (
        f"📊 Decyzje eksperymentalnych portfeli AI | {data.strftime('%d.%m')}\n"
        f"#GPW\n"
        f"⚖️ Nie stanowi rekomendacji inwestycyjnej. Eksperymentalne portfele AI."
    )
    return {"is_thread": False, "tweets": [fallback]}
