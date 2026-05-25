"""
agents/xpost_agent.py — backward-compat re-export shell.

PUBLICZNE API: niżej wymienione symbole. Wszystkie generatory zostały
rozbite w Fazie 4 redesignu (2026-04-17) na moduł `agents/xpost/`:

  agents/xpost/base.py             — _SYSTEM, _SECTOR_EMOJI, _sector_emoji,
                                     _date_label, _call_gemini
  agents/xpost/formatters.py       — _fmt_top/_fmt_list/_fmt_index, _extract_tweet,
                                     _strip_tld_suffix, _strip_trailing_ticker_hashtags,
                                     _merge_top_announcements, _TLD_SUFFIX_RE
  agents/xpost/templates.py        — _SINGLE/_THREAD/_INDEX_DAILY/_SATURDAY/
                                     _SUNDAY/_QUOTES_TEMPLATE
  agents/xpost/intraday.py         — generate_xpost (premarket/morning/afternoon/
                                     afterhours/daily_thread)
  agents/xpost/weekly_saturday.py  — generate_xpost_saturday
  agents/xpost/weekly_sunday.py    — generate_xpost_sunday
  agents/xpost/index_daily.py      — generate_xpost_index_daily, _trim_index_tweet,
                                     _INDEX_EMOJI/_INDEX_ORDER/_INDEX_TWEET_MAX_CHARS
  agents/xpost/quotes.py           — generate_xpost_quotes, filter_quotes_gemini,
                                     _normalize_accepted
  agents/xpost/agenda.py           — generate_xpost_agenda, _build_agenda_fallback
  agents/xpost/weekly_dividends.py — generate_xpost_weekly_dividends,
                                     _build_weekly_dividends_fallback
  agents/xpost/weekly_agenda.py    — generate_xpost_weekly_agenda,
                                     _build_weekly_agenda_fallback
  agents/xpost/regenerate.py       — regenerate_with_suggestions

Nowe callerzy POWINNI importować bezpośrednio z `agents.xpost.<module>`
zamiast `agents.xpost_agent` (re-export tu zostaje tylko dla istniejącego
kodu — `xpost.py`, testy, bo migracja byłaby zbyt szeroka).
"""
from __future__ import annotations

# Re-exporty z agents/xpost/ — backward compat dla istniejących callerów.
from agents.xpost.agenda import (  # noqa: F401
    _build_agenda_fallback,
    generate_xpost_agenda,
)
from agents.xpost.base import (  # noqa: F401
    _SECTOR_EMOJI,
    _SYSTEM,
    _call_gemini,
    _date_label,
    _sector_emoji,
)
from agents.xpost.broker_decisions import (  # noqa: F401
    generate_xpost_broker_decisions,
)
from agents.xpost.formatters import (  # noqa: F401
    _TLD_SUFFIX_RE,
    _extract_tweet,
    _fmt_index,
    _fmt_list,
    _fmt_top,
    _merge_top_announcements,
    _strip_tld_suffix,
    _strip_trailing_ticker_hashtags,
)
from agents.xpost.index_daily import (  # noqa: F401
    _INDEX_EMOJI,
    _INDEX_ORDER,
    _INDEX_TWEET_MAX_CHARS,
    _trim_index_tweet,
    generate_xpost_index_daily,
)
from agents.xpost.intraday import generate_xpost  # noqa: F401
from agents.xpost.quotes import (  # noqa: F401
    _normalize_accepted,
    filter_quotes_gemini,
    generate_xpost_quotes,
)
from agents.xpost.regenerate import regenerate_with_suggestions  # noqa: F401
from agents.xpost.templates import (  # noqa: F401
    _INDEX_DAILY_TEMPLATE,
    _QUOTES_TEMPLATE,
    _SATURDAY_TEMPLATE,
    _SINGLE_TEMPLATE,
    _SUNDAY_TEMPLATE,
    _THREAD_TEMPLATE,
)
from agents.xpost.weekly_agenda import (  # noqa: F401
    _build_weekly_agenda_fallback,
    generate_xpost_weekly_agenda,
)
from agents.xpost.weekly_dividends import (  # noqa: F401
    _build_weekly_dividends_fallback,
    generate_xpost_weekly_dividends,
)
from agents.xpost.weekly_saturday import generate_xpost_saturday  # noqa: F401
from agents.xpost.weekly_sunday import generate_xpost_sunday  # noqa: F401
