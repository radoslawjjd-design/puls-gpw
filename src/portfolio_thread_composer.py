"""Deterministic formatting for portfolio-status X threads (PUL-39).

Kept out of any Gemini call on purpose — the figures here are already extracted
and delta-computed, so an LLM would only add paraphrase/rounding risk on numbers
that are already exact (same reasoning as Phase 3's thread-drafting addendum).
"""
from dataclasses import dataclass
from datetime import date

from src.gemini_client import PortfolioPosition

WALLET_LABELS: dict[str, tuple[str, str]] = {
    "main": ("Główny", "🏦"),
    "ikze": ("IKZE", "🏛️"),
    "short": ("Short", "📉"),
    "long": ("Long", "📈"),
}

_LEADER_MIN_PROFIT_ABS = 500.0
_LEADER_MAX_COUNT = 3
_ROCKET_CUMULATIVE_PCT = 100.0
_ROCKET_DAILY_PCT = 5.0
_DISCLAIMER = "Screeny z apki. Nie jest to rekomendacja ani porada inwestycyjna."


@dataclass
class WalletThreadData:
    wallet: str
    total_value: float
    currency: str
    total_profit_abs: float
    positions: list[PortfolioPosition]
    day_change_abs: float | None = None
    day_change_pct: float | None = None


def format_pln(value: float) -> str:
    """Polish number format: comma decimal, space-separated thousands."""
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):,.2f}".replace(",", " ").partition(".")
    return f"{sign}{whole},{frac}"


def _signed(value: float) -> str:
    return f"+{format_pln(value)}" if value >= 0 else format_pln(value)


def _cumulative_pct(profit_abs: float, value: float) -> float | None:
    cost = value - profit_abs
    if cost == 0:
        return None
    return profit_abs / cost * 100


def _wallet_label(wallet: str) -> tuple[str, str]:
    return WALLET_LABELS.get(wallet, (wallet.title(), "💼"))


def _compose_header_tweet(thread_date: date, wallets: list[WalletThreadData]) -> str:
    combined_label = "+".join(_wallet_label(w.wallet)[0] for w in wallets)
    combined_emoji = _wallet_label(wallets[0].wallet)[1]
    combined_total = sum(w.total_value for w in wallets)
    combined_profit = sum(w.total_profit_abs for w in wallets)

    lines = [
        f"📊 Portfel | {thread_date.strftime('%d.%m.%Y')} 🧵",
        f"{combined_emoji} {combined_label}: {format_pln(combined_total)} zł",
        f"📈 Zysk: {_signed(combined_profit)} zł",
    ]
    for w in wallets:
        label, emoji = _wallet_label(w.wallet)
        pct = _cumulative_pct(w.total_profit_abs, w.total_value)
        pct_part = f" ({_signed(pct)}%)" if pct is not None else ""
        lines.append(f"{emoji} {label}: {format_pln(w.total_value)} zł{pct_part}")
        if w.day_change_abs is not None and w.day_change_pct is not None:
            rocket = " 🚀" if w.day_change_pct > _ROCKET_DAILY_PCT else ""
            lines.append(
                f"   Dzisiaj: {_signed(w.day_change_abs)} zł ({_signed(w.day_change_pct)}%){rocket}"
            )
    return "\n".join(lines)


def _select_leaders(positions: list[PortfolioPosition]) -> list[PortfolioPosition]:
    eligible = [p for p in positions if p.profit_abs is not None and p.profit_abs > _LEADER_MIN_PROFIT_ABS]
    eligible.sort(key=lambda p: p.profit_abs, reverse=True)
    return eligible[:_LEADER_MAX_COUNT]


def _compose_leaders_tweet(wallets: list[WalletThreadData]) -> str:
    lines = ["🏆 Liderzy portfela (skumulowane):"]
    for w in wallets:
        label, emoji = _wallet_label(w.wallet)
        leaders = _select_leaders(w.positions)
        if not leaders:
            continue
        lines.append(f"{emoji} {label}:")
        for p in leaders:
            pct = _cumulative_pct(p.profit_abs, p.value)
            rocket = " 🚀" if pct is not None and pct > _ROCKET_CUMULATIVE_PCT else ""
            pct_part = f" ({_signed(pct)}%{rocket})" if pct is not None else ""
            lines.append(f"{p.ticker} {_signed(p.profit_abs)} zł{pct_part}")
    lines.append("—")
    lines.append(_DISCLAIMER)
    hashtags = ["#GPW"] + [f"#{_wallet_label(w.wallet)[0].upper()}" for w in wallets if w.wallet == "ikze"]
    lines.append(" ".join(hashtags))
    return "\n".join(lines)


def compose_portfolio_thread(thread_date: date, wallets: list[WalletThreadData]) -> list[str]:
    """Returns [header_tweet, leaders_tweet] for the given wallets (1 thread)."""
    return [
        _compose_header_tweet(thread_date, wallets),
        _compose_leaders_tweet(wallets),
    ]
