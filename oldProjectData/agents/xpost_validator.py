"""
Supervisor jakości postów X — ocenia wygenerowane posty przed wysyłką preview.

Flow:
  validate_xpost(post_data, window, source_data) → ValidationResult
    1. Sprawdzenie techniczne (encoding, długość, kompletność, forbidden words) — bez AI
    2. Ocena Gemini supervisor (score 1-10, w tym ZGODNOŚĆ Z DANYMI)

Progi:
  score >= 6 → passed=True  → normalny preview email (od 2026-04-20: zmiana > 6 na ≥6)
  score < 6  → passed=False → alert email + regeneracja z sugestiami

Reguły (CHAR_LIMITS, FORBIDDEN_PATTERNS, TECH_ARTIFACTS) importowane z config.py
— jedno źródło prawdy dla generatora i walidatora.
"""
import logging
import re
from dataclasses import dataclass, field

from config import (
    XPOST_CHAR_LIMITS as CHAR_LIMITS,
)
from config import (
    XPOST_DISCLAIMER as DISCLAIMER,
)
from config import (
    XPOST_FORBIDDEN_WORDS,
    XPOST_TECH_ARTIFACTS,
)

logger = logging.getLogger(__name__)

# Kompilacja forbidden patterns z config (raz przy imporcie)
_FORBIDDEN_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), desc)
    for pattern, desc in XPOST_FORBIDDEN_WORDS
]

# ── Prompt supervisora (zwięzły — reguły NIE powtarzane z generatora) ────────

_VALIDATOR_SYSTEM = """Jesteś supervisorem jakości postów finansowych na platformę X.
Oceniasz posty GPW pod kątem jakości redakcyjnej ORAZ zgodności z danymi źródłowymi.
Zwracasz WYŁĄCZNIE poprawny JSON."""

_VALIDATOR_TEMPLATE = """
DATA: {today}
Wyniki za poprzedni rok/kwartał są poprawne (np. w marcu 2026 wyniki za 2025 to norma).

<dane_zrodlowe>
{source_data_block}
</dane_zrodlowe>

<post_do_oceny>
Okno: {window} | Typ: {typ} | Tweetów: {n_tweets}

{tresc}
</post_do_oceny>

=== PROCEDURA WERYFIKACJI ===
Przed oceną wykonaj te kroki:
1. Wylistuj KAŻDY ticker ($TICKER) z postu
2. Sprawdź czy każdy ticker jest w danych źródłowych — pamiętaj o aliasach!
   Sekcja "TICKERY w danych" pokazuje pary `KOD_GPW (NAZWA_SPÓŁKI)`.
   $LWB w poście == BOGDANKA w danych = NIE halucynacja (to ten sam podmiot).
   $PKO == PKOBP, $CDR == CDPROJEKT, $LPP == LPP itd.
3. Wylistuj KAŻDĄ kwotę/procent z postu
4. Sprawdź czy każda kwota jest w danych źródłowych — jeśli NIE → halucynacja
5. Sprawdź czy disclaimer jest w ostatnim tweecie

=== KRYTERIA OCENY (1–10) ===

1. ZGODNOŚĆ Z DANYMI (waga 50%) — NAJWAŻNIEJSZE
   Ticker/kwota/fakt w poście, których NIE MA w danych = HALUCYNACJA → max 3/10.
   Jedna halucynacja = max 3/10 niezależnie od reszty.
   UWAGA: kody GPW ($LWB, $CDR, $MWAR) NIE są halucynacją gdy spółka jest w danych
   pod nazwą (BOGDANKA, CDPROJEKT, MOSTALWAR) — to standardowe aliasy giełdowe.

2. KONKRETNOŚĆ (20%) — liczby z danych. Brak liczb ale brak wymyślonych = 6/10.

3. INFORMATYWNOŚĆ (10%) — czy czytelnik dowiaduje się czegoś o GPW? Czy są $TICKER?

4. JĘZYK I STYL (10%) — poprawna polszczyzna, zero artefaktów technicznych.

5. DISCLAIMER — ostatni tweet MUSI zawierać "Nie stanowi rekomendacji inwestycyjnej".
   Brak = -2 pkt.

6. STRUKTURA (10%) — wizualne sekcje z emoji, markery, puste linie.
   Ściana tekstu = max 5/10.

7. DŁUGOŚĆ (X 2026 hard limits — NIGDY nie sugeruj więcej niż 280 zn.):
   - KAŻDY post (single i każdy w wątku): max 280 znaków (twardy limit X).
   - Idealne: 71-100 zn. (krótkie) lub 240-270 zn. (długie).
   - Hook (pierwszy post wątku) MOŻE być krótki (30-100 zn.) — tak ma być, nie obniżaj.
   - Posty <30 zn. = za mało treści (-1 pkt).
   - Posty >280 zn. = compliance fail (-3 pkt).

8. ZAKAZANE TREŚCI (bezwzględne → max 3/10):
   - Statystyki sentymentu "X pozytywnych / Y negatywnych"
   - Słowa "pozytywne"/"negatywne" jako nagłówki sekcji
   - Słowo "sentyment"
   - Porady inwestycyjne (kupuj, sprzedaj, warto, potencjał)

Progi: 8-10 = publikuj, 7 = OK, 5-6 = regeneruj, 1-4 = halucynacje/niedopuszczalny.

JSON:
{{
  "score": <int 1-10>,
  "uzasadnienie": "<co dobre, co złe>",
  "problemy": ["<problem 1>", "<problem 2>"],
  "sugestie": "<jak poprawić; które dane są halucynacją; NIE sugeruj długości >280 zn.>",
  "halucynacje": ["<wymyślony ticker/kwota #1>", "<#2>"]
}}"""


# ── Wynik walidacji ──────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    score: int                        # 1-10 (Gemini) lub 2 (błąd techniczny)
    passed: bool                      # score >= 6 (od 2026-04-20)
    uzasadnienie: str         = ""
    problemy: list[str]       = field(default_factory=list)
    sugestie: str             = ""
    technical_ok: bool        = True
    technical_issues: list[str] = field(default_factory=list)
    char_counts: list[int]    = field(default_factory=list)  # znaki per tweet
    attempt: int              = 1                             # która próba
    validator_fallback: bool  = False                         # True = Gemini niedostępny
    halucynacje: list[str]    = field(default_factory=list)   # wykryte halucynacje


# ── Sprawdzenie techniczne ────────────────────────────────────────────────────

def _check_technical(
    tweets: list[str],
    is_thread: bool,
    window: str = "",
    source_tickers: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """
    Sprawdza artefakty techniczne, długość, forbidden words i kompletność.

    Args:
        source_tickers: opcjonalna lista nazw spółek z source_data (np.
            ["BOGDANKA", "MOSTALWAR"]). Jeśli podana, dla każdego $TICKER
            w poście validator sprawdza czy ticker matchuje którąś nazwę
            (przez `name_to_ticker` mapping). Gdy nie matchuje + ticker jest
            znany w GPW (np. $MWT vs $MSW dla MOSTALWAR) → halucynacja
            kontekstowa (F6.6).

    Zwraca (ok, lista_problemów).
    """
    # ── Cashtag v2 opt-in (2026-04-28+) ─────────────────────────────────────
    # Okna w XPOST_CASHTAG_V2_WINDOWS używają position-aware limits:
    #   hook 280 / body 500 / closing 400 + soft warningi cashtag.
    # Inne okna — istniejące zachowanie (single source of CHAR_LIMITS).
    from config import XPOST_CASHTAG_V2_WINDOWS

    # v2 format dotyczy TYLKO threadów (single post = fallback 1-company,
    # używa starego template _SINGLE_TEMPLATE i starych limitów 280 zn).
    # Sunday WYKLUCZONY z position-aware limits — Premium long-form
    # (do 5000 zn / post) ma własne CHAR_LIMITS["premium_long"]. Sunday
    # dostaje cashtag rules tylko przez prompt suffix (weekly_sunday.py).
    is_cashtag_v2 = (
        window in XPOST_CASHTAG_V2_WINDOWS
        and is_thread
        and len(tweets) >= 2
        and window != "sunday"
    )
    position_limits = None  # ustawione tylko dla v2

    if is_cashtag_v2:
        from agents.xpost.cashtag_rules import (
            TWEET_LENGTH_LIMITS,
        )
        position_limits = TWEET_LENGTH_LIMITS
        # `limits` używane w fallback (gdy position niezidentyfikowane)
        limits = CHAR_LIMITS["thread"] if is_thread else CHAR_LIMITS["single"]
    elif window == "index_daily":
        limits = CHAR_LIMITS["index_thread"]
    elif window == "agenda":
        limits = CHAR_LIMITS["agenda"]
    elif window == "sunday":
        # X Premium long-form (do 5000 zn / post) — Weekly Outlook
        # z pełną agendą per dzień (decyzja user 2026-04-19).
        limits = CHAR_LIMITS["premium_long"]
    else:
        limits = CHAR_LIMITS["thread"] if is_thread else CHAR_LIMITS["single"]
    issues = []

    # F6.6: Pre-compute zestaw dozwolonych KODÓW GPW (z source_data).
    # _SYSTEM wymaga kodów GPW ($LWB), NIE nazw ($BOGDANKA = błąd).
    # Dla source_tickers=["BOGDANKA", "MOSTALWAR", "ZUE"]:
    #   allowed_tickers = {"LWB", "MSW", "ZUE"}
    # Gdy `name_to_ticker(name)` None ALE nazwa == kod GPW → akceptuj nazwę.
    # Gdy nazwa nieznana (np. "AFHOL" — AFORTI Holding nie w company_list):
    #   → akceptuj NAZWĘ jako cashtag ($AFHOL) — Strategy A fallback.
    #   → soft fallback: też wszystkie kody GPW (validator nie zna mappingu).
    allowed_tickers: set[str] | None = None
    if source_tickers:
        from utils.gpw_tickers import get_gpw_tickers, name_to_ticker
        gpw_set = get_gpw_tickers()
        allowed_tickers = set()
        unknown_count = 0
        for raw in source_tickers:
            name = (raw or "").strip().upper()
            if not name:
                continue
            kod = name_to_ticker(name)
            if kod:
                allowed_tickers.add(kod)  # tylko kod GPW (np. LWB dla BOGDANKA)
            elif name in gpw_set:
                allowed_tickers.add(name)  # nazwa == kod GPW (np. ZUE)
            else:
                # Nieznana spółka — akceptuj jej NAZWĘ jako cashtag fallback.
                # Strategy A w intraday.py wstrzykuje "$AFHOL" jako wymagany
                # ticker dla AFHOL — validator musi to przepuścić.
                allowed_tickers.add(name)
                # Dla wieloczłonowych nazw (espiebi: "FARMY FOTOWOLTAIKI POLSKA")
                # Gemini używa pierwszego słowa jako cashtag ($FARMY).
                # Dodaj je też do allowed_tickers żeby nie odrzucać poprawnych postów.
                words = name.split()
                if len(words) > 1 and len(words[0]) >= 2:
                    allowed_tickers.add(words[0])
                unknown_count += 1
        # PR#14 #4 fix (2026-04-20): NIE expanduj do całego GPW gdy unknown.
        # Wcześniej `allowed_tickers | gpw_set` (~430 tickerów) → degraduje
        # F6.6 hard-fail do "akceptuj cokolwiek z whitelist". Jeśli Gemini
        # użyje $JSW (prawdziwy ticker) ale spoza source_data — fałszywie
        # przepuszczone. Teraz scope tight: tylko nazwy z source_data + ich
        # GPW kody + nazwy fallback dla unknown (linia wyżej).
        if unknown_count > 0:
            logger.warning(
                f"F6.6 soft fallback aktywny: {unknown_count} nieznanych spółek w "
                f"source_tickers (niemapowanych w name_to_ticker). Akceptuję ich "
                f"NAZWY jako cashtag fallback, ale NIE expanduję do całego GPW."
            )

    for i, tweet in enumerate(tweets):
        label = f"Tweet {i+1}" if is_thread else "Post"

        # Artefakty CSS/HTML
        for artifact in XPOST_TECH_ARTIFACTS:
            if artifact.lower() in tweet.lower():
                issues.append(f"{label}: artefakt techniczny '{artifact}'")
                break

        # Długość
        n = len(tweet)
        if is_cashtag_v2 and position_limits is not None:
            # Position-aware limits: hook 280 / body 500 / closing 400
            from agents.xpost.cashtag_rules import classify_position
            pos = classify_position(i, len(tweets))
            pl = position_limits[pos]
            if n < pl["min"]:
                issues.append(
                    f"{label} ({pos}): za krótki ({n} znaków, min {pl['min']})"
                )
            elif n > pl["hard_max"]:
                issues.append(
                    f"{label} ({pos}): za długi ({n} znaków, max {pl['hard_max']})"
                )
        else:
            if n < limits["min"]:
                issues.append(f"{label}: za krótki ({n} znaków, min {limits['min']})")
            elif n > limits["max"]:
                issues.append(f"{label}: za długi ({n} znaków, max {limits['max']})")

        # Zabronione słowa/nagłówki (z centralnej config)
        # Sprawdzaj PRZED disclaimerem (disclaimer zawiera "rekomendacji inwestycyjnej")
        tweet_before_disclaimer = tweet.split(DISCLAIMER)[0] if DISCLAIMER in tweet else tweet
        for pattern, desc in _FORBIDDEN_PATTERNS:
            if pattern.search(tweet_before_disclaimer):
                issues.append(f"{label}: zawiera zakazane wyrażenie — {desc}")
                break

        # Kompletność — tweet nie powinien być obcięty w połowie zdania
        stripped = tweet.rstrip()
        if stripped and not _is_complete_text(stripped):
            issues.append(
                f"{label}: tekst wygląda na obcięty w połowie zdania "
                f"(kończy się na: '...{stripped[-30:]}')"
            )

        # Unikalność tickerów w treści (bullet-prefix `$TICKER —`).
        # Adaptive rule:
        # - Stare okna (NIE w cashtag-v2): max 1 per ticker (legacy strict).
        # - Cashtag-v2 okna: max 3 per ticker (multi-cashtag intentional dla
        #   różnych aspektów tej samej spółki — briefing 2026-04-28 zaleca
        #   gdy naturalnie pasuje). ≥4 = spam → hardfail.
        body_ticker_pattern = re.compile(r"[#$]([A-Z][A-Z0-9]{2,})\s*[—–\-]")
        body_tickers = body_ticker_pattern.findall(tweet)
        if body_tickers:
            from collections import Counter
            counts = Counter(body_tickers)
            max_per_ticker = 3 if is_cashtag_v2 else 1
            spam = [(t, c) for t, c in counts.items() if c > max_per_ticker]
            if spam:
                dup_str = ", ".join(f"{t}×{c}" for t, c in spam)
                if is_cashtag_v2:
                    issues.append(
                        f"{label}: za dużo powtórzeń tickera — {dup_str} "
                        f"(max {max_per_ticker} per spółka per post; "
                        f"≥4 = spam)"
                    )
                else:
                    issues.append(
                        f"{label}: powtórzony ticker w treści — {dup_str} "
                        f"(1 wpis per spółka per post)"
                    )

        # F6.6: halucynacja kontekstowa cashtagów (gdy source_tickers podane)
        # Każdy $TICKER w treści musi być KODEM GPW (np. $LWB nie $BOGDANKA)
        # i matchować spółkę z source_data. Regex {1,11} łapie też długie
        # nazwy które Gemini może zwrócić zamiast kodu (np. $MOSTALWAR = 9 zn,
        # $BOGDANKA = 8 zn) — żeby validator je też wykrył jako halucynacje.
        if allowed_tickers is not None:
            cashtag_pattern = re.compile(r"\$([A-Z][A-Z0-9]{1,11})\b")
            for cashtag in cashtag_pattern.findall(tweet):
                if cashtag.upper() not in allowed_tickers:
                    issues.append(
                        f"{label}: cashtag ${cashtag} nie odpowiada żadnej "
                        f"spółce z danych źródłowych. Użyj kodu GPW. "
                        f"Dozwolone: {', '.join('$'+t for t in sorted(allowed_tickers))}"
                    )
                    break  # 1 issue per tweet wystarczy

        # ── Cashtag v2: anty-spam guard (>10 cashtagów = algo X karze) ─────
        if is_cashtag_v2:
            from agents.xpost.cashtag_rules import count_cashtags
            n_cashtags = count_cashtags(tweet)
            if n_cashtags > 10:
                issues.append(
                    f"{label}: za dużo cashtagów ({n_cashtags}) — spam signal, "
                    f"algo X karze >10. Sweet spot 5-7 w closing."
                )

    # ── Cashtag v2: closing tweet musi mieć ≥min(2, n_unique_cashtags) ─────
    # Closing bez cashtagów = strata zasięgu (TIER 1 z briefingu).
    # Adaptive min: jeśli cały thread ma tylko 1 unique cashtag (np. broker
    # z 1 decyzją tygodnia), wymóg redukowany do 1 — fizycznie nie da się
    # więcej. Dla ≥2 spółek w threadzie → min 2 (jak briefing zaleca 5-7).
    if is_cashtag_v2 and tweets:
        from agents.xpost.cashtag_rules import (
            CLOSING_CASHTAG_MIN,
            count_cashtags,
            unique_cashtags,
        )
        # Zsumuj unique cashtagi z całego threadu (poza closing — body+hook)
        thread_uniques: set[str] = set()
        for t in tweets[:-1]:
            thread_uniques |= unique_cashtags(t)
        # Jeśli body+hook mają tylko 1 unique → closing też max 1 (1-decision case)
        adaptive_min = min(CLOSING_CASHTAG_MIN, max(1, len(thread_uniques)))
        last = tweets[-1]
        last_n = count_cashtags(last)
        if last_n < adaptive_min:
            label = f"Tweet {len(tweets)}/{len(tweets)}" if is_thread else "Post"
            issues.append(
                f"{label} (closing): tylko {last_n} cashtagów — "
                f"closing wymaga ≥{adaptive_min} (TOP movers + $TICKER)."
            )

    # Disclaimer prawny — wymagany w ostatnim tweecie
    last_tweet = tweets[-1] if tweets else ""
    if DISCLAIMER not in last_tweet:
        issues.append(
            f"{f'Tweet {len(tweets)}/{len(tweets)}'  if is_thread else 'Post'}: "
            f"brak disclaimera '{DISCLAIMER}'"
        )

    return len(issues) == 0, issues


def _is_complete_text(text: str) -> bool:
    """
    Sprawdza czy tekst kończy się kompletnie (nie jest obcięty w połowie zdania).
    Zwraca True jeśli tekst wygląda na kompletny.
    """
    text = text.rstrip()
    if not text:
        return True

    # Poprawne zakończenia tekstu
    valid_endings = (
        ".", "!", "?", "…",        # interpunkcja końcowa
        ")", "]", "\"", "'", "»",  # zamknięcia
        "🧵",                       # emoji wątku
        "#GPW", "#ESPI", "#giełda", "#makro",  # hashtagi
        "EBI.", "EBI",              # disclaimer
        "inwestycyjnej",           # disclaimer: "Nie stanowi rekomendacji inwestycyjnej"
        "/7",                       # numeracja wątku (1/7, 2/7, ...)
        "📊", "📈", "📉", "💡", "⚖️",  # emoji sekcji
    )

    last_char = text[-1]

    # Emoji na końcu — OK
    if ord(last_char) > 0x2000:
        return True

    # Zakończenie na jednym z poprawnych stringów
    for ending in valid_endings:
        if text.endswith(ending):
            return True

    # Zakończenie na cyfrze (np. "2025", "30%") — OK
    if last_char.isdigit() or last_char == "%":
        return True

    # Zakończenie na hashtagu (np. "#TSGAMES", "#KGHM") — OK
    import re
    if re.search(r"#[A-Za-z0-9]+$", text):
        return True

    return False


# ── Format danych źródłowych ────────────────────────────────────────────────

def _format_source_data(source_data: dict | None) -> str:
    """Formatuje dane źródłowe do wstawienia w prompt validatora.

    UWAGA: pole "TICKERY w danych" może zawierać NAZWY spółek (BOGDANKA, CDPROJEKT)
    z BQ analyses, nie kody GPW. Validator + Gemini supervisor MUSI wiedzieć że
    cashtag $TICKER w poście (np. $LWB, $CDR) to KOD GPW odpowiadający nazwie
    z danych. Dlatego listujemy obie formy: `$KOD (NAZWA)`.
    """
    from utils.gpw_tickers import name_to_ticker

    if not source_data:
        return "(brak danych źródłowych — nie można zweryfikować zgodności)"

    lines = []

    # Tickery — pokazujemy parę KOD_GPW + nazwa (alias). Gemini ma akceptować $KOD.
    tickers = source_data.get("tickers", [])
    if tickers:
        pairs = []
        for raw in tickers:
            n = (raw or "").strip().upper()
            kod = name_to_ticker(n)
            if kod and kod != n:
                pairs.append(f"${kod} ({n})")
            else:
                # Już jest kod GPW lub brak mappingu — pokaż jak jest
                pairs.append(f"${n}")
        lines.append(
            "TICKERY w danych (format `$KOD (NAZWA)` — cashtag w poście == kod GPW): "
            + ", ".join(pairs)
        )

    # Najważniejsze ogłoszenia (flat list — bez etykiet sentymentu)
    items = source_data.get("top_ogłoszenia", [])
    if items:
        lines.append("\nNAJWAŻNIEJSZE OGŁOSZENIA:")
        for item in items:
            ticker = item.get("spolka", "?")
            tytul = item.get("tytul", "")
            dlaczego = item.get("dlaczego_wazne", "")
            fakty = item.get("kluczowe_fakty") or []
            line = f"  • {ticker}: {tytul}"
            if fakty:
                line += "\n    FAKTY: " + " | ".join(str(f)[:150] for f in fakty[:5])
            elif dlaczego:
                line += f"\n    → {dlaczego}"
            lines.append(line)

    # Sektory (realne statystyki z BQ)
    sektory = source_data.get("sektory", [])
    if sektory:
        lines.append("\nAKTYWNE SEKTORY:")
        for s in sektory:
            lines.append(f"  • {s.get('sektor', '?')}: {s.get('liczba_ogloszen', 0)} ogł.")

    # Liczba ogłoszeń
    liczba = source_data.get("liczba_ogloszen", 0)
    if liczba:
        lines.append(f"\nLICZBA OGŁOSZEŃ: {liczba}")

    return "\n".join(lines) if lines else "(brak danych źródłowych)"


def build_source_data(
    top_pozytywne: list[dict],
    top_negatywne: list[dict],
    sentyment: dict | None = None,
    liczba_ogloszen: int = 0,
    sektory: list[dict] | None = None,
) -> dict:
    """
    Buduje słownik source_data z danych wejściowych xpost generation.
    Używany przez xpost.py do przekazania kontekstu do validatora.

    NOTE: Wewnętrznie scala top_pozytywne/top_negatywne w flat list
    "top_ogłoszenia" — bez etykiet sentymentu w danych dla validatora.
    """
    tickers = set()
    merged = []
    for items in [top_pozytywne, top_negatywne]:
        for item in items:
            ticker = item.get("spolka", "")
            if ticker and ticker != "?":
                tickers.add(ticker.replace("-", ""))
            merged.append(item)

    return {
        "tickers": sorted(tickers),
        "top_ogłoszenia": merged,
        "liczba_ogloszen": liczba_ogloszen,
        "sektory": sektory or [],
    }


# ── Główna funkcja walidacji ─────────────────────────────────────────────────

def validate_xpost(
    post_data: dict,
    window: str,
    attempt: int = 1,
    source_data: dict | None = None,
) -> ValidationResult:
    """
    Waliduje post X: technicznie + Gemini supervisor (z danymi źródłowymi).

    Args:
        post_data:    {"tweets": [...], "is_thread": bool}
        window:       "premarket" | "morning" | "afternoon" | "afterhours" | "daily_thread"
        attempt:      numer próby (1 = pierwsza generacja, 2+ = po regeneracji)
        source_data:  dane źródłowe do weryfikacji zgodności (tickery, kwoty)

    Returns:
        ValidationResult
    """
    from agents.vertex_client import call_gemini_json

    tweets    = post_data.get("tweets", [])
    is_thread = post_data.get("is_thread", False)
    char_counts = [len(t) for t in tweets]

    if not tweets:
        return ValidationResult(
            score=0, passed=False, attempt=attempt,
            uzasadnienie="Brak tweetów do oceny",
            technical_ok=False,
            technical_issues=["Brak treści"],
        )

    # ── 1. Sprawdzenie techniczne (z F6.6 ticker context check) ─────────────
    src_tickers = (source_data or {}).get("tickers") if source_data else None
    tech_ok, tech_issues = _check_technical(
        tweets, is_thread, window=window, source_tickers=src_tickers,
    )

    if not tech_ok:
        logger.warning(f"Walidacja techniczna FAILED (próba {attempt}): {tech_issues}")
        return ValidationResult(
            score=2, passed=False, attempt=attempt,
            uzasadnienie="Problemy techniczne uniemożliwiające publikację",
            problemy=tech_issues,
            sugestie=(
                f"Znalezione problemy techniczne: {'; '.join(tech_issues)}. "
                "Wyeliminuj wskazane zakazane słowa/frazy. "
                "KAŻDY post MUSI być ≤ 280 znaków (X 2026 hard limit). "
                "Idealne: 71-100 zn. lub 240-270 zn. "
                "Upewnij się że każdy tweet kończy się pełnym zdaniem i ma disclaimer."
            ),
            technical_ok=False,
            technical_issues=tech_issues,
            char_counts=char_counts,
        )

    # ── 1b. Sunday Premium long-form: skip Gemini scoring ───────────────────
    # Analogicznie do quotes — Gemini validator template hardcoduje 280 zn
    # i "ostatni tweet z disclaimer" co dla 7-tweet Premium long-form thread
    # daje false positives ("ściana tekstu", "halucynacja agregatów").
    # Compliance guard (max 1 cashtag/post, no .pl/.com) + _check_technical
    # (F6.6 ticker context) wystarczą jako defense in depth.
    if window == "sunday":
        logger.info(
            f"Walidacja sunday: skip Gemini scoring (Premium long-form). "
            f"technical_ok=True, char_counts={char_counts}"
        )
        return ValidationResult(
            score=8, passed=True, attempt=attempt,
            uzasadnienie=(
                "Sunday Premium long-form: skip Gemini scoring. "
                "Compliance guard + _check_technical (F6.6) jako defense in depth."
            ),
            technical_ok=True,
            char_counts=char_counts,
        )

    # ── 2. Ocena jakościowa przez Gemini (z danymi źródłowymi) ──────────────
    typ   = "wątek (7 tweetów)" if is_thread else "pojedynczy post"
    tresc = (
        "\n\n---\n\n".join(
            f"[Tweet {i+1}/{len(tweets)}] ({len(t)} znaków)\n{t}"
            for i, t in enumerate(tweets)
        )
        if is_thread
        else f"({char_counts[0]} znaków)\n{tweets[0]}"
    )

    import zoneinfo
    from datetime import datetime
    today_str = datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")

    source_data_block = _format_source_data(source_data)

    if not source_data:
        logger.warning(
            f"⚠️ Walidacja BEZ danych źródłowych (window={window}) — "
            f"supervisor nie może zweryfikować zgodności tickerów/kwot"
        )
    else:
        n_tickers = len(source_data.get("tickers", []))
        n_items = len(source_data.get("top_ogłoszenia", []))
        logger.info(
            f"Walidacja z danymi źródłowymi: {n_tickers} tickerów, "
            f"{n_items} ogłoszeń w referencji"
        )

    prompt = _VALIDATOR_SYSTEM + "\n\n" + _VALIDATOR_TEMPLATE.format(
        window=window,
        typ=typ,
        n_tweets=len(tweets),
        tresc=tresc,
        today=today_str,
        source_data_block=source_data_block,
    )

    logger.debug(f"Prompt supervisora ({len(prompt)} znaków):\n{prompt[:500]}...")

    try:
        raw = call_gemini_json(
            prompt,
            max_retries=1,
            metadata={
                "agent":   "xpost_validator",
                "window":  window,
                "attempt": attempt,
                "is_thread": is_thread,
                "n_tweets":  len(tweets),
            },
            # Phase 4 re-enabled 2026-04-23: thinking_budget=768 (50% bufor vs
            # oryginalny plan 512). Scoring xposta — zadanie średnio-proste.
            # max_output_tokens=8192: safety net dla MAX_TOKENS (thinking + output).
            thinking_budget=768,
            max_output_tokens=8192,
        )
        if not raw or "score" not in raw:
            raise ValueError("Brak pola 'score' w odpowiedzi Gemini")

        score  = max(1, min(10, int(raw.get("score", 5))))
        # 2026-04-20: zlagodzenie z > 6 → >= 6. Score 6 = "OK ale są sugestie",
        # nie powinien blokować publikacji. Halucynacje (max 3/10) i tech fail
        # (score 2) nadal blokowane (oba < 6). Po smoke: premarket thread
        # z DIGITANET dostawal score=6 mimo poprawnego contentu.
        passed = score >= 6

        halucynacje = [str(h) for h in raw.get("halucynacje", [])]

        vr = ValidationResult(
            score=score,
            passed=passed,
            uzasadnienie=str(raw.get("uzasadnienie", "")),
            problemy=[str(p) for p in raw.get("problemy", [])],
            sugestie=str(raw.get("sugestie", "")),
            technical_ok=True,
            char_counts=char_counts,
            attempt=attempt,
            halucynacje=halucynacje,
        )

        logger.info(
            f"Walidacja (próba {attempt}): score={score}/10 → "
            f"{'✓ OK' if passed else '✗ SŁABY'} | {vr.uzasadnienie[:80]}"
        )
        if halucynacje:
            logger.warning(f"Wykryte halucynacje: {halucynacje}")
        return vr

    except Exception as e:
        logger.error(f"Błąd walidatora Gemini (próba {attempt}): {e}")
        # Przy błędzie walidatora nie blokuj — przepuść post, ale oznacz fallback
        logger.warning(
            "⚠️ Walidacja Gemini niedostępna — post przepuszczony bez oceny (score=7)"
        )
        return ValidationResult(
            score=7, passed=True, attempt=attempt,
            uzasadnienie=f"Walidator niedostępny ({e}) — post przepuszczony bez oceny",
            technical_ok=True,
            char_counts=char_counts,
            validator_fallback=True,
        )
