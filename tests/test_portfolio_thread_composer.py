from datetime import date

from src.gemini_client import PortfolioPosition
from src.portfolio_thread_composer import (
    WalletThreadData,
    compose_portfolio_thread,
    format_pln,
)


def _position(ticker: str, value: float, profit_abs: float | None, pct: float | None = None) -> PortfolioPosition:
    return PortfolioPosition(ticker=ticker, value=value, pct=pct, profit_abs=profit_abs)


def test_format_pln_uses_polish_separators():
    assert format_pln(54442.99) == "54 442,99"
    assert format_pln(-21.58) == "-21,58"
    assert format_pln(0) == "0,00"


def test_header_tweet_combines_two_wallets_with_cumulative_pct():
    wallets = [
        WalletThreadData(
            wallet="main", total_value=37217.26, currency="PLN",
            total_profit_abs=4000.0, positions=[],
        ),
        WalletThreadData(
            wallet="ikze", total_value=17225.73, currency="PLN",
            total_profit_abs=2167.79, positions=[],
        ),
    ]
    header, _ = compose_portfolio_thread(date(2026, 5, 29), wallets)

    assert "29.05.2026" in header
    assert "Główny+IKZE" in header
    assert "54 442,99 zł" in header  # combined total
    assert "+6 167,79 zł" in header  # combined profit
    assert "Główny: 37 217,26 zł" in header
    assert "IKZE: 17 225,73 zł" in header


def test_header_tweet_omits_day_change_when_no_prior_data():
    wallets = [
        WalletThreadData(wallet="main", total_value=1000.0, currency="PLN", total_profit_abs=0.0, positions=[]),
    ]
    header, _ = compose_portfolio_thread(date.today(), wallets)

    assert "Dzisiaj" not in header


def test_leaders_tweet_filters_sorts_and_caps_at_three():
    positions = [
        _position("A", value=1000.0, profit_abs=200.0),  # below 500 threshold, excluded
        _position("B", value=2000.0, profit_abs=1000.0),
        _position("C", value=3000.0, profit_abs=2000.0),
        _position("D", value=1500.0, profit_abs=600.0),
        _position("E", value=1800.0, profit_abs=800.0),
    ]
    wallets = [WalletThreadData(wallet="main", total_value=9300.0, currency="PLN", total_profit_abs=4600.0, positions=positions)]
    _, leaders = compose_portfolio_thread(date.today(), wallets)

    assert "C " in leaders
    assert "B " in leaders
    assert "E " in leaders
    assert "D " not in leaders  # 4th by profit_abs, capped at top 3
    assert "A " not in leaders  # below 500 zł threshold


def test_leaders_tweet_uses_plain_ticker_no_cashtag():
    positions = [_position("XTB", value=2000.0, profit_abs=1000.0)]
    wallets = [WalletThreadData(wallet="main", total_value=2000.0, currency="PLN", total_profit_abs=1000.0, positions=positions)]
    _, leaders = compose_portfolio_thread(date.today(), wallets)

    assert "$XTB" not in leaders
    assert "XTB" in leaders


def test_leaders_tweet_adds_rocket_above_100_percent_cumulative():
    positions = [_position("DGN", value=3741.0, profit_abs=2728.50)]  # ~269% cumulative
    wallets = [WalletThreadData(wallet="main", total_value=3741.0, currency="PLN", total_profit_abs=2728.50, positions=positions)]
    _, leaders = compose_portfolio_thread(date.today(), wallets)

    assert "🚀" in leaders


def test_leaders_tweet_carries_disclaimer_and_hashtags_not_header():
    wallets = [WalletThreadData(wallet="main", total_value=1000.0, currency="PLN", total_profit_abs=0.0, positions=[])]
    header, leaders = compose_portfolio_thread(date.today(), wallets)

    assert "rekomendacj" in leaders.lower()
    assert "#GPW" in leaders
    assert "rekomendacj" not in header.lower()


def test_leaders_tweet_adds_ikze_hashtag_only_when_ikze_present():
    main_only = [WalletThreadData(wallet="main", total_value=1000.0, currency="PLN", total_profit_abs=0.0, positions=[])]
    with_ikze = main_only + [WalletThreadData(wallet="ikze", total_value=1000.0, currency="PLN", total_profit_abs=0.0, positions=[])]

    _, leaders_main_only = compose_portfolio_thread(date.today(), main_only)
    _, leaders_with_ikze = compose_portfolio_thread(date.today(), with_ikze)

    assert "#IKZE" not in leaders_main_only
    assert "#IKZE" in leaders_with_ikze
