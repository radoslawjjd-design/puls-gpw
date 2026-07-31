"""Round-trip test for time-aware holdings in the calendar and value chart (PUL-103).

Mocked tests do not execute SQL and the e2e suite replaces both functions
wholesale, so NOTHING in the repo can catch an arithmetic error in these two
queries — see the reserved-keyword lesson in context/foundation/lessons.md.
This script is the only net.

Runs against THROWAWAY tables (<name>_rt_<uuid>) by monkeypatching the
module-level table-name constants; the real tables are never touched. All five
tables both queries read are swapped, or real rows leak into the arithmetic and
the assertions stop being deterministic.

Every check is chosen so that a regression to "today's share counts on every
day" FAILS it:
  1. day before the buy      -> no row at all
  2. buy day                 -> shares x close, not today's shares x close
  3. after a partial sell    -> reduced shares
  4. a later month's buy     -> invisible to an earlier month (F1: horizon)
  5. operation off the spine -> still counted (F1: no equality join on dates)
  6. ticker sold to zero     -> present in history, gone after the sale
  7. oversell                -> absorbed, never negative shares
  8. residual ticker (cash)  -> constant across the window
  9. deposit before 1st buy  -> no row (F2: would render as a real flat 0 PLN)
 10. wallet with no operations -> bounded by user_portfolios.created_at
 11. last day                -> exactly today's shares (PUL-100 right-edge invariant)
 12. value chart             -> same holdings, same bound
 13. BOCF disjointness       -> a ticker with operations contributes nothing before
                                its first buy even when the backward fill has a price
                                for it, while a residual ticker still benefits
Plus a residual report and a wall-clock budget for both queries.

Run with:
    uv run python scripts/test_bq_portfolio_time_dimension.py

Requires ADC: gcloud auth application-default login
"""
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import db.bigquery as bq
from db.bigquery import _get_client, _table_ref, get_portfolio_calendar_data, get_portfolio_history
from google.cloud import bigquery as gbq

USER = f"rt-user-{uuid.uuid4().hex[:8]}"

PF_CORE = "rt-pf-core"          # buy / sell / later buy / cash residual / deposit day
PF_OFFSPINE = "rt-pf-offspine"  # an operation dated off the trading-day spine
PF_CLOSED = "rt-pf-closed"      # ticker sold to zero, no position row survives
PF_OVERSELL = "rt-pf-oversell"  # a sell with no matching buy in the window
PF_MANUAL = "rt-pf-manual"      # no operations at all
PF_DEBUT = "rt-pf-debut"        # tickers whose quotes start mid-window (BOCF)

# Ten weekdays in March plus one day in April. 2025-03-08 is a Saturday and is
# deliberately NOT here — it is where the off-spine operation lands.
SPINE = [
    date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6), date(2025, 3, 7),
    date(2025, 3, 10), date(2025, 3, 11), date(2025, 3, 12), date(2025, 3, 13), date(2025, 3, 14),
    date(2025, 4, 1),
]
OFF_SPINE_DAY = date(2025, 3, 8)

# close, daily change. Flat on purpose: any wrong number is then unambiguously a
# wrong share count, never a wrong price.
PRICES = {
    "AAA": (100.0, 1.0),
    "BBB": (50.0, 0.5),
    "CCC": (20.0, 0.2),
    "DDD": (10.0, 0.1),
    "EEE": (30.0, 0.3),
    "FFF": (60.0, 0.6),
    "GGG": (25.0, 0.25),
    "HHH": (40.0, 0.4),
}

# Quotes that start mid-window, so the backward fill has something to do. FFF is
# bought on its first quoted day (a ticker WITH operations must contribute nothing
# before that, however well the fill can price it); GGG is held throughout and has
# no operations at all (a residual ticker must still be backward-filled).
DEBUT_DAY = date(2025, 3, 11)
LATE_DEBUT = {"FFF": DEBUT_DAY, "GGG": DEBUT_DAY}

CASH = bq.CASH_TICKER

# Both queries are user-facing; the chart already measures ~1.6s before this
# change adds CTEs. Generous enough not to flap on a cold slot, tight enough to
# notice a quadratic join.
QUERY_BUDGET_SECONDS = 20.0

_EPS = 0.005


def _ts(d: date, hour: int = 10) -> str:
    """A trading-hours stamp. UTC 10:00 is inside the Warsaw session either way,
    so the Europe/Warsaw bucketing cannot drift to a neighbouring day."""
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc).isoformat()


def _make_throwaway(const_name: str, schema) -> tuple[str, str, str]:
    client = _get_client()
    real_name = getattr(bq, const_name)
    rt_name = f"{real_name}_rt_{uuid.uuid4().hex[:8]}"
    rt_ref = _table_ref(client, rt_name)
    table = gbq.Table(rt_ref, schema=schema)
    # expires must be set BEFORE create_table, or the table outlives the run.
    table.expires = datetime.now(timezone.utc) + timedelta(hours=24)
    client.create_table(table)
    setattr(bq, const_name, rt_name)
    return const_name, real_name, rt_ref


def _load(rt_ref: str, schema, rows: list[dict]) -> None:
    if not rows:
        return
    client = _get_client()
    job = client.load_table_from_json(
        rows, rt_ref,
        job_config=gbq.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND"),
    )
    job.result()


def _price_rows() -> list[dict]:
    rows = []
    for ticker, (close, chg) in PRICES.items():
        for d in SPINE:
            if ticker in LATE_DEBUT and d < LATE_DEBUT[ticker]:
                continue
            rows.append({
                "ticker": ticker,
                "snapshot_date": d.isoformat(),
                "kurs_zamkniecia": close,
                "zmiana_procentowa": 1.0,
                "zmiana_kwotowa": chg,
                "fetched_at": _ts(d),
                "source": "gpw",
            })
    return rows


def _position(portfolio_id: str, ticker: str, shares: float, price: float = 1.0) -> dict:
    return {
        "user_id": USER,
        "ticker": ticker,
        "company_name": ticker,
        "shares": shares,
        "avg_buy_price": price,
        "created_at": _ts(date(2025, 3, 1)),
        "updated_at": _ts(date(2025, 3, 1)),
        "portfolio_id": portfolio_id,
    }


def _op(portfolio_id: str, op_type: str, when: date, ticker: str | None = None,
        volume: float | None = None, amount: float = 0.0) -> dict:
    return {
        "user_id": USER,
        "portfolio_id": portfolio_id,
        "broker": "xtb",
        "external_id": f"{portfolio_id}:{op_type}:{ticker}:{when.isoformat()}:{uuid.uuid4().hex[:6]}",
        "op_type": op_type,
        "occurred_at": _ts(when),
        "imported_at": _ts(date(2026, 7, 30)),
        "amount_pln": amount,
        "raw_type": op_type,
        "ticker": ticker,
        "volume": volume,
        "unit_price": PRICES[ticker][0] if ticker in PRICES else None,
        "source_file": "roundtrip.xlsx",
    }


def _portfolio(portfolio_id: str, created: date, order: int) -> dict:
    return {
        "user_id": USER,
        "portfolio_id": portfolio_id,
        "portfolio_type": "glowny",
        "portfolio_name": portfolio_id,
        "display_order": order,
        "created_at": _ts(created),
    }


def _seed(refs: dict[str, str]) -> None:
    _load(refs["_COMPANY_DAILY_STATS_TABLE_NAME"], bq._COMPANY_DAILY_STATS_SCHEMA, _price_rows())

    _load(refs["_USER_PORTFOLIOS_TABLE_NAME"], bq._USER_PORTFOLIOS_SCHEMA, [
        _portfolio(PF_CORE, date(2025, 1, 2), 1),
        _portfolio(PF_OFFSPINE, date(2025, 1, 2), 2),
        _portfolio(PF_CLOSED, date(2025, 1, 2), 3),
        _portfolio(PF_OVERSELL, date(2025, 1, 2), 4),
        # The only wallet whose bound comes from here: it has no operations.
        _portfolio(PF_MANUAL, date(2025, 3, 10), 5),
        _portfolio(PF_DEBUT, date(2025, 1, 2), 6),
    ])

    _load(refs["_USER_PORTFOLIO_POSITIONS_TABLE_NAME"], bq._USER_PORTFOLIO_POSITIONS_SCHEMA, [
        # 10 bought, 4 sold, 100 bought again in April -> 106 today.
        _position(PF_CORE, "AAA", 106.0, 100.0),
        _position(PF_CORE, CASH, 250.0, 1.0),
        _position(PF_OFFSPINE, "BBB", 2.0, 50.0),
        # PF_CLOSED holds nothing: the import deletes a fully sold ticker.
        # Bought 8 before the export window, sold 5 inside it.
        _position(PF_OVERSELL, "DDD", 3.0, 10.0),
        _position(PF_MANUAL, "EEE", 7.0, 30.0),
        # HHH opens the wallet (so inception does not hide the pre-debut days we
        # want to inspect); GGG is the residual; FFF is bought on its debut day.
        _position(PF_DEBUT, "HHH", 5.0, 40.0),
        _position(PF_DEBUT, "GGG", 2.0, 25.0),
        _position(PF_DEBUT, "FFF", 4.0, 60.0),
    ])

    _load(refs["_USER_BROKER_OPERATIONS_TABLE_NAME"], bq._USER_BROKER_OPERATIONS_SCHEMA, [
        # Deposit two days before the first buy — must NOT open the calendar.
        _op(PF_CORE, "cash", date(2025, 3, 3), amount=5000.0),
        _op(PF_CORE, "buy", date(2025, 3, 5), "AAA", 10.0, amount=-1000.0),
        _op(PF_CORE, "sell", date(2025, 3, 11), "AAA", 4.0, amount=400.0),
        # April: invisible to March, decisive for the horizon check.
        _op(PF_CORE, "buy", date(2025, 4, 1), "AAA", 100.0, amount=-10000.0),

        _op(PF_OFFSPINE, "buy", date(2025, 3, 5), "BBB", 5.0, amount=-250.0),
        _op(PF_OFFSPINE, "sell", OFF_SPINE_DAY, "BBB", 3.0, amount=150.0),

        _op(PF_CLOSED, "buy", date(2025, 3, 5), "CCC", 8.0, amount=-160.0),
        _op(PF_CLOSED, "sell", date(2025, 3, 12), "CCC", 8.0, amount=160.0),

        _op(PF_OVERSELL, "sell", date(2025, 3, 6), "DDD", 5.0, amount=50.0),

        _op(PF_DEBUT, "buy", date(2025, 3, 4), "HHH", 5.0, amount=-200.0),
        _op(PF_DEBUT, "buy", DEBUT_DAY, "FFF", 4.0, amount=-240.0),
    ])


def _calendar(portfolio_id: str, year: int, month: int) -> dict[date, dict]:
    return {r["snapshot_date"]: r for r in get_portfolio_calendar_data(portfolio_id, USER, year, month)}


def _expect_absent(rows: dict[date, dict], day: date, why: str) -> None:
    assert day not in rows, f"{why}: {day} returned a row {rows[day]}, expected none"


def _expect(rows: dict[date, dict], day: date, value: float, positions: int, why: str) -> None:
    assert day in rows, f"{why}: {day} returned no row, expected value {value}"
    got = rows[day]
    assert abs(got["portfolio_value"] - value) < _EPS, (
        f"{why}: {day} value {got['portfolio_value']:.2f}, expected {value:.2f}"
    )
    assert got["total_positions"] == positions, (
        f"{why}: {day} total_positions {got['total_positions']}, expected {positions}"
    )


def _check_core() -> None:
    march = _calendar(PF_CORE, 2025, 3)

    _expect_absent(march, date(2025, 3, 3), "deposit day must not open the calendar (F2)")
    _expect_absent(march, date(2025, 3, 4), "day before the first buy")
    print("OK: nothing before the first buy — the deposit day stays closed")

    # 10 AAA @ 100 + 250 cash. Today's shares would give 10600 + 250.
    _expect(march, date(2025, 3, 5), 1250.0, 2, "buy day")
    _expect(march, date(2025, 3, 10), 1250.0, 2, "still holding 10")
    chg = march[date(2025, 3, 5)]["daily_change_pln"]
    assert abs(chg - 10.0) < _EPS, f"buy-day change {chg:.2f}, expected 10.00 (10 shares x 1.00)"
    print("OK: buy day valued at the shares held that day")

    # 6 AAA @ 100 + 250 cash after selling 4.
    _expect(march, date(2025, 3, 11), 850.0, 2, "after the partial sell")
    chg = march[date(2025, 3, 11)]["daily_change_pln"]
    assert abs(chg - 6.0) < _EPS, f"post-sell change {chg:.2f}, expected 6.00"
    print("OK: partial sell reduces the day's weight")

    # THE horizon check (F1). A window over the March spine cannot see the April
    # buy, so it would report 106 shares here — 10850 instead of 850.
    _expect(march, date(2025, 3, 14), 850.0, 2, "March must not see the April buy (F1 horizon)")
    print("OK: a later month's buy is invisible to an earlier month")

    # Cash has no operations, so it is the residual: constant on every day. Isolate
    # its contribution by subtracting the equity leg, which differs between the two
    # days (10 shares before the sale, 6 after).
    cash_early = march[date(2025, 3, 5)]["portfolio_value"] - 10 * PRICES["AAA"][0]
    cash_late = march[date(2025, 3, 14)]["portfolio_value"] - 6 * PRICES["AAA"][0]
    assert abs(cash_early - cash_late) < _EPS, (
        f"residual drifted: {cash_early:.2f} on 03-05 vs {cash_late:.2f} on 03-14"
    )
    assert abs(cash_early - 250.0) < _EPS, f"residual is {cash_early:.2f}, expected 250.00"
    assert march[date(2025, 3, 14)]["prices_found"] == 2, "cash must stay priced across the window"
    print(f"OK: residual ticker (cash) held constant at {cash_early:.2f}")

    # PUL-100 right edge: the last day equals today's stored share count.
    april = _calendar(PF_CORE, 2025, 4)
    _expect(april, date(2025, 4, 1), 106 * 100.0 + 250.0, 2, "right edge equals today's shares")
    print("OK: right edge equals today's share count (PUL-100 invariant)")


def _check_off_spine() -> None:
    march = _calendar(PF_OFFSPINE, 2025, 3)
    _expect(march, date(2025, 3, 7), 250.0, 1, "before the Saturday sell")
    # Joining operations to the spine by date equality would drop the 03-08 sell
    # and leave 5 shares here.
    _expect(march, date(2025, 3, 10), 100.0, 1, "operation dated off the spine (F1)")
    print("OK: an operation dated off the trading-day spine is still counted")


def _check_closed_ticker() -> None:
    march = _calendar(PF_CLOSED, 2025, 3)
    # No position row survives for CCC, so the ticker universe must come from the
    # operations as well, or this wallet has no history at all.
    _expect(march, date(2025, 3, 11), 160.0, 1, "ticker sold to zero is present in history")
    _expect_absent(march, date(2025, 3, 12), "sold-out ticker leaves the day it is sold")
    print("OK: a ticker sold to zero appears in history and leaves on the sale")


def _check_oversell() -> None:
    march = _calendar(PF_OVERSELL, 2025, 3)
    # 3 held today, 5 sold inside the window, the matching buy predates the
    # export. A naive forward sum would report -5 shares.
    _expect(march, date(2025, 3, 6), 30.0, 1, "oversell absorbed by the residual")
    for day, row in march.items():
        assert row["portfolio_value"] >= -_EPS, f"negative value {row['portfolio_value']} on {day}"
    print("OK: a sell with no matching buy never produces negative shares")


def _check_manual_wallet() -> None:
    march = _calendar(PF_MANUAL, 2025, 3)
    _expect_absent(march, date(2025, 3, 7), "before the wallet was created")
    _expect(march, date(2025, 3, 10), 210.0, 1, "wallet with no operations keeps its shares")
    _expect(march, date(2025, 3, 14), 210.0, 1, "wallet with no operations is flat, not empty")
    print("OK: a wallet with no operations is bounded by its creation date, not emptied")


def _check_history() -> None:
    result = get_portfolio_history(PF_CORE, USER, date(2025, 3, 1))
    series = {r["snapshot_date"]: r for r in result["series"]}

    assert date(2025, 3, 3) not in series, "value chart must not start before the first buy"
    assert date(2025, 3, 4) not in series, "value chart must not start before the first buy"
    for day, expected in ((date(2025, 3, 5), 1250.0), (date(2025, 3, 14), 850.0),
                          (date(2025, 4, 1), 10850.0)):
        assert day in series, f"value chart missing {day}"
        got = series[day]["value_pln"]
        assert abs(got - expected) < _EPS, f"chart value on {day} is {got:.2f}, expected {expected:.2f}"
    print("OK: value chart carries the same holdings and the same bound")

    # The requested range starts two days before the wallet did, and the index-based
    # X axis cannot show that on its own.
    assert result["data_from"] == date(2025, 3, 5), (
        f"data_from is {result['data_from']}, expected 2025-03-05"
    )
    print("OK: a range the wallet could not fill is announced, not silently stretched")

    # A wallet whose whole history fits inside the range says nothing.
    quiet = get_portfolio_history(PF_CORE, USER, date(2025, 3, 5))
    assert quiet["data_from"] is None, f"data_from should be None, got {quiet['data_from']}"
    print("OK: a range the wallet fills completely carries no note")


def _check_bocf_disjointness() -> None:
    """The three corrections must not overlap (plan-review F3).

    Shares decide *whether* a ticker is held, the price fill decides *at what price*.
    A ticker with operations holds nothing before its first buy, so the backward fill
    multiplies by zero however good a price it found; a residual ticker keeps its
    shares and genuinely needs the fill.  If the two ever compounded, this is the only
    place it would show.
    """
    result = get_portfolio_history(PF_DEBUT, USER, date(2025, 3, 1))
    series = {r["snapshot_date"]: r for r in result["series"]}

    hhh, ggg, fff = 5 * 40.0, 2 * 25.0, 4 * 60.0

    assert date(2025, 3, 3) not in series, "before the wallet's first buy"
    # 03-04..03-10: FFF has a backward-filled price but zero shares -> contributes 0.
    # GGG has no operations, so it keeps its 2 shares and the fill is what prices them.
    for day in (date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 10)):
        got = series[day]["value_pln"]
        assert abs(got - (hhh + ggg)) < _EPS, (
            f"pre-debut {day} is {got:.2f}, expected {hhh + ggg:.2f} "
            f"(a ticker with operations must contribute nothing before its first buy)"
        )
    got = series[DEBUT_DAY]["value_pln"]
    assert abs(got - (hhh + ggg + fff)) < _EPS, (
        f"debut day is {got:.2f}, expected {hhh + ggg + fff:.2f}"
    )
    print("OK: BOCF prices the residual ticker and multiplies the ops ticker by zero")

    # P&L must not read the pre-debut days as free profit: the backward fill prices
    # GGG exactly at its own basis here, so the only non-zero leg would be a ticker
    # valued without a basis.
    for day in (date(2025, 3, 4), DEBUT_DAY):
        pnl = series[day]["pnl_pln"]
        assert abs(pnl) < _EPS, f"pnl on {day} is {pnl:.2f}, expected 0.00"
    print("OK: every valued ticker carries a cost basis, including the backward-filled one")


# Every (wallet, ticker) whose share count the operations are NOT expected to explain,
# named one by one rather than by wallet: a residual that appears where none was seeded
# means the reconstruction lost something.
_EXPECTED_RESIDUALS = {
    (PF_CORE, CASH),        # cash carries no operation, ever
    (PF_MANUAL, "EEE"),     # entered by hand, never imported
    (PF_OVERSELL, "DDD"),   # the matching buy predates the export window
    (PF_DEBUT, "GGG"),      # seeded residual, exists to exercise the backward fill
}


def _check_residual_report() -> None:
    """Surface every (wallet, ticker) the operations do not explain.

    The ticket asks for the divergence to be disclosed rather than silently
    absorbed. Absorbing it is the right behaviour — this is where it gets said
    out loud.
    """
    client = _get_client()
    ops = _table_ref(client, bq._USER_BROKER_OPERATIONS_TABLE_NAME)
    pos = _table_ref(client, bq._USER_PORTFOLIO_POSITIONS_TABLE_NAME)
    rows = list(client.query(f"""
        WITH recon AS (
          SELECT portfolio_id, ticker,
                 SUM(CASE WHEN op_type='buy' THEN volume
                          WHEN op_type='sell' THEN -volume ELSE 0 END) AS recon
          FROM `{ops}` WHERE user_id = @u AND ticker IS NOT NULL
          GROUP BY portfolio_id, ticker
        ),
        held AS (SELECT portfolio_id, ticker, shares FROM `{pos}` WHERE user_id = @u)
        SELECT COALESCE(r.portfolio_id, h.portfolio_id) AS pf,
               COALESCE(r.ticker, h.ticker) AS tk,
               ROUND(COALESCE(h.shares, 0) - COALESCE(r.recon, 0), 6) AS residual
        FROM recon r FULL OUTER JOIN held h
          ON r.portfolio_id = h.portfolio_id AND r.ticker = h.ticker
        ORDER BY pf, tk
    """, job_config=gbq.QueryJobConfig(query_parameters=[
        gbq.ScalarQueryParameter("u", "STRING", USER)
    ])).result())

    print("\n  residual report (today's shares - reconstruction):")
    unexpected = []
    for row in rows:
        if abs(row.residual) < 1e-9:
            continue
        expected = (row.pf, row.tk) in _EXPECTED_RESIDUALS
        tag = "expected" if expected else "UNEXPECTED"
        print(f"    {row.pf:<16} {row.tk:<6} {row.residual:>10.4f}  {tag}")
        if not expected:
            unexpected.append((row.pf, row.tk, row.residual))
    assert not unexpected, f"unexplained residuals: {unexpected}"
    print(f"  ({len(_EXPECTED_RESIDUALS)} seeded residuals, nothing else)")


def _check_timing() -> None:
    started = time.monotonic()
    get_portfolio_calendar_data(PF_CORE, USER, 2025, 3)
    calendar_s = time.monotonic() - started

    started = time.monotonic()
    get_portfolio_history(PF_CORE, USER, date(2025, 3, 1))
    history_s = time.monotonic() - started

    print(f"\n  calendar {calendar_s:.1f}s, history {history_s:.1f}s "
          f"(budget {QUERY_BUDGET_SECONDS:.0f}s each)")
    assert calendar_s < QUERY_BUDGET_SECONDS, f"calendar took {calendar_s:.1f}s"
    assert history_s < QUERY_BUDGET_SECONDS, f"history took {history_s:.1f}s"
    print("OK: both queries inside the wall-clock budget")


_TABLES = [
    ("_COMPANY_DAILY_STATS_TABLE_NAME", "_COMPANY_DAILY_STATS_SCHEMA"),
    ("_ETF_QUOTES_TABLE_NAME", "_ETF_QUOTES_SCHEMA"),
    ("_USER_PORTFOLIO_POSITIONS_TABLE_NAME", "_USER_PORTFOLIO_POSITIONS_SCHEMA"),
    ("_USER_BROKER_OPERATIONS_TABLE_NAME", "_USER_BROKER_OPERATIONS_SCHEMA"),
    # Not in the plan's list of four: the inception fallback reads this table, so
    # without swapping it the manual-wallet bound is not deterministic.
    ("_USER_PORTFOLIOS_TABLE_NAME", "_USER_PORTFOLIOS_SCHEMA"),
]


def main() -> None:
    client = _get_client()
    restore: list[tuple[str, str]] = []
    refs: dict[str, str] = {}
    try:
        for const_name, schema_name in _TABLES:
            _, real_name, rt_ref = _make_throwaway(const_name, getattr(bq, schema_name))
            restore.append((const_name, real_name))
            refs[const_name] = rt_ref

        _seed(refs)
        print(f"seeded {len(SPINE)} trading days across 6 wallets for {USER}\n")

        _check_core()
        _check_off_spine()
        _check_closed_ticker()
        _check_oversell()
        _check_manual_wallet()
        _check_history()
        _check_bocf_disjointness()
        _check_residual_report()
        _check_timing()
        print("\nAll time-dimension round-trip checks passed.")
    finally:
        for const_name, real_name in restore:
            setattr(bq, const_name, real_name)
        for rt_ref in refs.values():
            client.delete_table(rt_ref, not_found_ok=True)
        print("OK: throwaway tables dropped")


if __name__ == "__main__":
    main()
