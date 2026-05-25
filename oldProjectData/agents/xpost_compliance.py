"""
Compliance guards dla pojedynczego posta X (Faza 2 redesignu).

Egzekwuje twarde reguły algorytmu X 2026:
- Max 1 cashtag ($TICKER) per post — X hard limit od 2026-Q1
- Max 2 hashtagi (#tag) per post — soft antyspam
- Max 280 znaków per post — twardy limit X

Weryfikacja listy tickerów GPW (czy $XYZ to prawdziwy kod) jest odpowiedzialnością
F3 (`utils/gpw_tickers.py` whitelist) — tu nie dotykamy.

Użycie:
    result = validate_compliance(post_text)
    if not result:
        raise ValueError(f"Post narusza reguły: {result.violations}")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from config import (
    XPOST_MAX_CASHTAGS_PER_POST,
    XPOST_MAX_CHARS_PER_POST,
    XPOST_MAX_HASHTAGS_PER_POST,
)

# Cashtag: `$` + 1-8 alfanumerycznych, musi zawierać co najmniej JEDNĄ literę.
# Dzięki temu:
# - $CDR, $ORL, $INGBSK → match (standardowe GPW, 3-6 liter)
# - $11B, $11BIT → match (tickery zaczynające się od cyfry, np. 11 bit studios)
# - $BOGDANKA (8) → match — wyłapujemy halucynacje Gemini (zamiast $LWB)
# - $1000, $50 → brak match (to kwota, nie ticker)
# Case-insensitive match (X normalizuje "$cdr" == "$CDR" — liczymy oba).
_CASHTAG_RE = re.compile(r"\$(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,8}\b")

# Hashtag: `#` + litera (a-zA-Z_) + więcej znaków alfanumerycznych.
# Musi zaczynać się od LITERY — to odfiltrowuje "#1", "#2" (numery rozdziałów).
# X też nie traktuje "#1" jako hashtagu.
_HASHTAG_RE = re.compile(r"#[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ComplianceResult:
    is_ok: bool
    violations: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.is_ok


def validate_compliance(
    post_text: str,
    known_tickers: frozenset[str] | set[str] | None = None,
    window: str = "",
) -> ComplianceResult:
    """
    Sprawdza czy pojedynczy post X jest zgodny z compliance 2026.

    Args:
        post_text: treść pojedynczego posta.
        known_tickers: opcjonalny zestaw znanych tickerów GPW.
        window: nazwa okna xpost (np. "sunday"). Dla X Premium long-form okien
            (sunday) zwiększamy limit cashtag do 8 i hashtag do 5 — long posts
            zmieszczą wiele spółek per dzień.

    Zwraca ComplianceResult z listą naruszeń (ludzkie zdania).
    """
    violations: list[str] = []

    # FIX 2026-04-19: window-aware limits dla X Premium long-form (sunday).
    # Free tier nadal max 1 cashtag / max 2 hashtag (X 2026).
    is_premium_long = window in ("sunday",)
    # FIX 2026-04-19 (po smoke #2): sunday Premium long NADAL max 1 cashtag per post
    # (user zasada — algorytm X penalizuje >1 cashtag per post nawet w Premium).
    # Spółki listujemy plain text wielką literą (INGBSK, OPONEO, BNPPPL) bez $.
    # Tylko 1 wyróżniona **$TICKER** per long post (top spółka dnia, kod GPW).
    max_cashtags = XPOST_MAX_CASHTAGS_PER_POST  # 1 — same dla wszystkich okien
    max_hashtags = 5 if is_premium_long else XPOST_MAX_HASHTAGS_PER_POST
    max_chars = 5000 if is_premium_long else XPOST_MAX_CHARS_PER_POST

    # 1. Length
    n_chars = len(post_text)
    if n_chars > max_chars:
        violations.append(
            f"Post ma {n_chars} znaków, limit {max_chars}."
        )

    # 2. Cashtags — count
    cashtags = _CASHTAG_RE.findall(post_text)
    n_cashtags = len(cashtags)
    if n_cashtags > max_cashtags:
        violations.append(
            f"Post zawiera {n_cashtags} cashtagów ({', '.join(cashtags[:5])}), "
            f"max {max_cashtags} per post."
        )

    # 2b. Cashtags — whitelist (jeśli podano) — SOFT warning, nie blokuje publish
    # PR fix 2026-04-20: wcześniej hard FAIL blokowało posty z NewConnect tickerami
    # (np. $NWAI, $FEMTECH — realne spółki spoza main market GPW whitelist).
    # F6.6 hard-fail w xpost_validator sprawdza kontekstowe halucynacje
    # (cashtag vs source_tickers), compliance guard ma tylko sprawdzać limits
    # (cashtag count, hashtag count, length). Unknown tickers → WARNING only.
    import logging as _logging
    if known_tickers is not None:
        unknown = [
            c for c in cashtags
            if c[1:].upper() not in known_tickers  # strip $ + upper
        ]
        if unknown:
            _logging.getLogger(__name__).warning(
                f"Compliance guard: cashtag(i) spoza GPW whitelist: {', '.join(unknown[:5])}. "
                f"Może być NewConnect / small cap / halucynacja Gemini. "
                f"F6.6 validator sprawdza kontekst vs source_tickers."
            )

    # 3. Hashtags
    hashtags = _HASHTAG_RE.findall(post_text)
    n_hashtags = len(hashtags)
    if n_hashtags > max_hashtags:
        violations.append(
            f"Post zawiera {n_hashtags} hashtagów ({', '.join(hashtags[:5])}), "
            f"zalecane max {max_hashtags} (antyspam)."
        )

    return ComplianceResult(is_ok=(len(violations) == 0), violations=violations)
