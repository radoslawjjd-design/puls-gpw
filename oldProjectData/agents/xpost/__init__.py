"""
Moduł generatorów postów X (refactor z monolitu agents/xpost_agent.py).

Faza 4 redesignu — rozbicie boskiego pliku na:
- base.py: shared constants (_SYSTEM, _SECTOR_EMOJI) + helpers (_call_gemini, _date_label)
- formatters.py: pure format helpers (_fmt_top/list/index, _extract_tweet, _strip_*)
- {window}.py: per-typ generatory (presession, afternoon, daily_thread, weekly, agenda, ...)

Publiczne API nadal dostępne przez `from agents.xpost_agent import ...` (backward compat).
"""
