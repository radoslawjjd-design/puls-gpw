"""
Priority matrix dla selekcji newsów do threadu (Faza 5 redesignu X).

Score per news (heurystyka na słowach kluczowych w tytule + faktach):
    going_concern         100  — zagrożenie kontynuacji działalności
    major_contract         80  — kontrakt > 50 mln zł
    dividend_ex_soon       75  — dywidenda z ex-date dziś/jutro
    m_and_a                70  — przejęcia, podziały, fuzje
    buyback                60  — skup akcji własnych, umorzenie
    capital_increase       55  — emisja, wyłączenie prawa poboru
    other                  10  — neutralna informacja korporacyjna

Bonus indeksu (dodawany do base score, max 100):
    WIG20                  +50
    mWIG40                 +35
    sWIG80                 +20

Adaptive thread length:
    >= 5 strong (≥50)  →  6 postów (hook + max 5 spółek + close)
    3-4 strong         →  5 postów (hook + 3 spółki + close)
    1-2 strong         →  3 posty (hook + 1-2 spółki + close)
    0 strong           →  0 (skip publikacji, log info)

Source of truth: documentation/X_STRATEGY.md "Priority matrix".
"""
from __future__ import annotations

import re

# Patterns dla score per kategoria (kolejność = priorytet, pierwszy match wygrywa).
_PATTERNS: list[tuple[int, list[str]]] = [
    (100, [
        # Going concern + odmiany polskie (z `\w*` na końcach!)
        r"zagroż\w*\s+kontynuacj",
        r"kontynuacj\w*\s+działalnoś\w*",
        r"upadłoś\w*",
        r"\blikwidacj\w*",            # likwidacja/likwidację/likwidacji/likwidacją
        r"ujemn\w*\s+kapitał\w*\s+własn\w*",
        r"istotn\w*\s+niepewnoś\w*",  # "biegły wskazał istotną niepewność"
    ]),
    (80, [
        # Kontrakt > 50 mln (waliduje obecnością "mln" w pobliżu liczby > 50)
        r"\b(?:wartoś\w*|kontrakt\w*|umow\w*|przetarg\w*)\b[^.]{0,200}\b\d{2,4}[,.]?\d*\s*(?:mln|mld)\b",
    ]),
    (75, [
        r"dywidend\w*\s*[-:.]?\s*\d",   # "dywidenda 0,30" lub "dywidendy: 4,50"
        r"rekomend\w+\s+(?:wypłat\w*\s+)?dywidend",  # "rekomenduje wypłatę dywidendy"
        r"dzień ustaleni\w*\s+praw\w*\s+do\s+dywidend",
        r"prawo\s+do\s+dywidend",
        r"\bex[\s-]date\b",
    ]),
    (70, [
        r"przejęci\w*",
        r"podziału?\s+(?:banku|spółki|grupy)",
        r"podpisano\s+umow\w*\s+przejęci\w*",
        r"połączeni\w*\s+(?:spółek|grup)",
        r"strategiczn\w*\s+inwestor\w*",  # "pozyskuje strategicznego inwestora"
        r"objął\s+\d+\s*%\s+(?:kapitał|akcj|udział)",  # "objął 50% kapitału"
    ]),
    (60, [
        r"skup\w*\s+akcji\s+własn\w*",
        r"buyback",
        r"umorzeni\w*\s+akcji",
    ]),
    (55, [
        r"podwyższeni\w*\s+kapitał\w*",
        r"wyłączeni\w*\s+praw\w*\s+poboru",
        r"now\w*\s+emisj\w*\s+akcji",
    ]),
    # F6.4 hotfix: wyniki finansowe — najczęstszy typ newsa GPW.
    # User feedback: "ULTGAMES rekordowy raport" było score=10 (other),
    # przez co afternoon dla 17.04 dostał tylko 1 strong news.
    (50, [
        r"\brekordow\w*\s+(?:wynik|raport|zysk|przychod|kwartał)",
        r"\bzysk\w*\s+(?:net\w+\s+)?(?:wzrós\w*|spadł\w*|wyni[óo]s\w*|\+\s*\d)",
        r"\bprzychod\w+\s+(?:wzrós\w*|spadł\w*|wyni[óo]s\w*|\+\s*\d)",
        r"\b(?:wzrost|spadek)\s+zysku\s+(?:net\w*|operacyjn\w*)",
        r"\bEBITDA\s+(?:wzrós\w*|spadł\w*|wyni[óo]s\w*|\+\s*\d)",
        r"\bstrata\s+net\w*",                 # "strata netto" (bez wymogu liczby tuż obok)
        r"\bsłab\w*\s+wynik\w*",              # "słabe wyniki"
        r"\braport\s+(?:roczn\w*|kwartaln\w*|półroczn\w*)\b.*\b\d{4}\b",
    ]),
]

_INDEX_BONUS = {
    "WIG20":  50,
    "mWIG40": 35,
    "sWIG80": 20,
}

_OTHER_SCORE = 10
_STRONG_THRESHOLD = 50  # newsy ze score >= 50 są "strong" (wliczają się do thread length)


def score_news(news: dict, indeks: str | None = None) -> int:
    """
    Heurystyka score newsu na podstawie tytułu + kluczowych faktów + indeksu.

    Args:
        news: dict z polami `tytul`, `kluczowe_fakty` (list[str])
        indeks: opcjonalny ticker indeksu ("WIG20", "mWIG40", "sWIG80") — bonus.

    Returns:
        Score 0-150 (cap 150 — going concern + WIG20 bonus może dać 150).
    """
    haystack_parts = [news.get("tytul", "") or ""]
    haystack_parts.extend(str(f) for f in (news.get("kluczowe_fakty") or []))
    haystack = " ".join(haystack_parts).lower()

    # Pierwszy pasujący pattern wygrywa (kolejność wg priorytetu).
    base = _OTHER_SCORE
    for pts, patterns in _PATTERNS:
        if any(re.search(p, haystack, re.IGNORECASE) for p in patterns):
            base = pts
            break

    bonus = _INDEX_BONUS.get(indeks or "", 0)
    return base + bonus


def rank_news(news_list: list[dict]) -> list[tuple[dict, int]]:
    """
    Zwraca listę krotek (news, score) posortowaną po score malejąco.

    Stabilna sortowanie — przy równym score zachowana oryginalna kolejność.
    """
    scored = [(n, score_news(n)) for n in news_list]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def target_thread_length(
    scored_news: list[tuple[dict, int]],
    max_companies: int = 4,
) -> int:
    """
    Adaptive thread length na podstawie liczby strong news (score >= 50).

    Args:
        scored_news: lista (news, score) z `rank_news`.
        max_companies: max ile spółek pokazać w środku threadu (default 4 = intraday).
            Dla `daily_thread` (flagowiec dnia) podaje się 6 (thread 8: hook+6+close).

    Returns:
        Liczba postów w threadzie:
        - hook + N spółek + close (gdzie N = min(strong_news_count, max_companies))
        - 0 gdy 0 strong news (skip publikacji)

        Bandy strong→thread (dla domyślnego max_companies=4):
        - ≥5 strong → 6 (hook + 4 spółek + close)
        - 3-4       → 5 (hook + 3 + close)
        - 1-2       → 3 (hook + 1 + close)
        - 0         → 0
    """
    n_strong = sum(1 for _, s in scored_news if s >= _STRONG_THRESHOLD)
    if n_strong == 0:
        return 0
    # Liczba spółek = min(strong_count, max_companies, threshold-band)
    if n_strong >= 5:
        n_companies = min(max_companies, n_strong)
    elif n_strong >= 3:
        n_companies = min(3, max_companies)
    else:  # 1-2
        n_companies = min(n_strong, max_companies)
    return n_companies + 2  # hook + N + close
