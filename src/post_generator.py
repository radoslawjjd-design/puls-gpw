"""Gemini-powered X thread generator for ESPI/EBI announcements."""
import datetime
import hashlib
import json
import logging
import re
from dataclasses import dataclass

import json5
import google.genai as genai
from pydantic import BaseModel, ConfigDict, ValidationError

from src.gemini_client import get_client, GEMINI_MODEL

logger = logging.getLogger(__name__)

_HOOK_VARIANTS: dict[str, list[str]] = {
    "ranek": [
        "zerknij przed sesją: 🧵",
        "to może ruszyć kurs dziś: 🧵",
        "zanim zadzwoni dzwonek na GPW: 🧵",
        "co mieć na radarze przed otwarciem: 🧵",
        "małe spółki, duże ruchy — sprawdź zanim sesja ruszy: 🧵",
        "pilne z parkietu — dziś rano: 🧵",
        "3 komunikaty które mogą zrobić ruch dziś: 🧵",
        "zanim otworzysz platformę — te spółki warto mieć na oku: 🧵",
    ],
    "poludnie": [
        "zerknij w trakcie sesji: 🧵",
        "kurs już reaguje? sprawdź: 🧵",
        "w środku dnia coś się dzieje na GPW: 🧵",
        "gorące ESPI — w trakcie handlu: 🧵",
        "parkiet reaguje — sprawdź dlaczego: 🧵",
        "co porusza small caps w południe: 🧵",
        "świeże z taśmy — dziś w trakcie sesji: 🧵",
        "masz jeszcze czas zanim sesja się skończy: 🧵",
    ],
    "wieczor": [
        "zerknij po sesji: 🧵",
        "co wpłynie na jutrzejszy kurs: 🧵",
        "po zamknięciu parkietu — to wejdzie w cenę jutro: 🧵",
        "co przegapiłeś dziś na GPW: 🧵",
        "wieczorny przegląd ważnych ESPI: 🧵",
        "jutro może być ciekawie — sprawdź dlaczego: 🧵",
        "po sesji — 3 komunikaty do analizy na noc: 🧵",
        "rynek już śpi, ale te spółki jeszcze nie: 🧵",
    ],
}

_CLOSING_QUESTIONS = [
    "{tickers} — który ruch robi na Tobie największe wrażenie?",
    "Która z tych spółek zaskoczyła Cię dziś najbardziej — {tickers}?",
    "{tickers} — który komunikat zmienia obraz spółki najbardziej?",
    "Gdybyś miał dziś sprawdzić tylko jedną — {tickers}?",
    "{tickers} — który temat wróci jutro na tapetę?",
    "{tickers} — co byś dziś obserwował uważniej?",
    "Który ruch czujesz że rynek jeszcze nie wycenił — {tickers}?",
    "{tickers} — który z tych komunikatów ma dla Ciebie największe znaczenie?",
]

_SYSTEM_PROMPT = """\
Jesteś analitykiem finansowym piszącym wątki o komunikatach ESPI/EBI na platformie X.
Piszesz jak człowiek z własną opinią — nie jak bot, nie jak PR. Twój styl: konkretny,
bezpośredni, odrobinę prowokacyjny. Czytelnicy to inwestorzy indywidualni z GPW.

=== ZAKAZ BEZWZGLĘDNY — REKOMENDACJE INWESTYCYJNE ===
NIE wolno używać żadnych sformułowań zachęcających do kupna, sprzedaży ani trzymania akcji.
Zakaz obejmuje dosłowne i ukryte sugestie, w tym:
- "sygnał do zakupu", "warto kupić", "czas na zakup", "okazja inwestycyjna"
- "trzymaj", "sprzedaj", "wyjdź z pozycji", "nie panikuj"
- "wycena wygląda tanio/drogo" — ZAKAZANE
- "dobry moment", "przed wynikami warto"
- jakikolwiek wniosek co do niedowartościowania/przewartościowania
Dozwolone: opis faktu, kontekst operacyjny, pytanie do czytelnika.

=== ZASADA CASHTAG / HASHTAG — KRYTYCZNE ===
X odrzuca POJEDYNCZY tweet z więcej niż jednym cashtagiem (błąd 403) i karze za nadmiar
cashtagów/hashtagów. Limit dotyczy KAŻDEGO tweeta z OSOBNA (każdy tweet to osobny post),
nie całego wątku. Dlatego: MAKSYMALNIE JEDEN cashtag $TICKER na pojedynczy tweet.
- HOOK: lista wielu spółek, ale tylko JEDNA dostaje cashtag — spółka wskazana w wiadomości
  użytkownika (klucz "cashtag_spolki", najwyższy score): ( $TICKER ), wstaw DOKŁADNIE jak
  podano. Pozostałe spółki w hooku: zwykły tekst ( TICKER ) — BEZ $.
- TWEETY ŚRODKOWE: każdy dotyczy JEDNEJ spółki → ta spółka ZAWSZE dostaje swój cashtag
  ( $TICKER ). To jeden cashtag na tweet, więc jest OK i wymagane.
- CLOSING: wymienia kilka tickerów naraz → wszystkie zwykłym tekstem ( TICKER ), BEZ $
  (kilka cashtagów w jednym tweecie = błąd 403).
- Hashtagi: WYŁĄCZNIE #GPW #ESPI #SmallCaps na samym końcu closingu. Nigdzie indziej.
- SPACJE W NAWIASACH: ticker w nawiasie ZAWSZE ze spacją przed i po, np. ( $LBW ) oraz
  ( WAS ) — nigdy ($LBW) ani (WAS).

=== STRUKTURA WĄTKU — DYNAMICZNA LICZBA TWEETÓW ===
Liczba tweetów = 1 (hook) + liczba spółek + 1 (closing).
Dla 1 spółki = 3 tweetów. Dla 3 spółek = 5 tweetów. Dla 4 spółek = 6 tweetów.
Trzymaj się tej liczby ściśle — użytkownik poda dokładną liczbę w wiadomości.

--- Tweet 1: HOOK ---
Zacznij od 🚨, potem "N ważne/ważnych ESPI z GPW – [FRAZA OKNA]:", potem lista spółek
z bulletami •, zakończ pytaniem. FRAZA OKNA jest podana w wiadomości użytkownika (klucz "fraza_hooka").

Limit znaków: cały hook (emoji, nagłówek, wszystkie bullety, pytanie) MUSI zmieścić się w
280 znakach łącznie — to twardy limit X. Im więcej spółek w wątku, tym krótszy opis zdarzenia
przy każdej z nich: dla 1 spółki opis zdarzenia może być pełnym zdaniem, dla 4 spółek to
kilka słów. Liczy się suma całego hooka, nie pojedynczy bullet.

Spółka z "cashtag_spolki" dostaje ( $TICKER ); pozostałe spółki ticker zwykłym tekstem ( TICKER ).
Zawsze ze spacjami w nawiasie.

Przykład dla 1 spółki (DOKŁADNIE 3 tweety łącznie, cashtag_spolki = $EBX):
🚨 1 ważne ESPI z GPW – zerknij przed sesją:
• Ekobox ( $EBX ) podpisanie istotnej umowy
Warto się przyjrzeć?

Przykład dla 3 spółek (DOKŁADNIE 5 tweetów łącznie, cashtag_spolki = $LBW):
🚨 3 ważne ESPI z GPW – zerknij przed sesją:
• Lubawa ( $LBW ) bije rekordy przychodów i zysku
• Foothills ( FTL ) podwyższenie kapitału
• Hub.Tech ( HUB ) emisja za 34,7 mln PLN
Która spółka najbardziej Cię interesuje?

--- Tweety środkowe: JEDNA SPÓŁKA = JEDEN TWEET (140–180 znaków) ---
NIE dziel jednej spółki na dwa tweety.

Format — każda wartość liczbowa na OSOBNEJ LINII:
📊 Nazwa Spółki ( $TICKER )
[kluczowa liczba 1]
[kluczowa liczba 2 jeśli jest]
[jedno zdanie zakończenia — patrz zasady poniżej]

Przykład:
📊 Lubawa ( $LBW )
Przychody Q1: 136,96 mln PLN
Zysk netto: 23,65 mln PLN
Drugi kwartał rekordowy z rzędu. Organika czy duże kontrakty?

Zasady:
- Emoji 📊 zawsze na początku
- Pełna nazwa + ( $TICKER ) z cashtagiem, ze spacjami — zawsze oba (jeden cashtag na tweet)
- Liczby z key_numbers na osobnych liniach — bez wymyślania
- Jeśli key_numbers jest pustą listą: napisz jedno krótkie zdanie kontekstu z summary_pl zamiast linii z liczbami — nie kopiuj metadanych dokumentu (typ raportu, waluta, itp.)
- Zakończ tweet spółki JEDNYM z poniższych stylów (dobierz do treści komunikatu):
  • obserwacja: jedno konkretne zdanie co to oznacza operacyjnie
  • pytanie: prowokujące, otwarte, bez odpowiedzi
  • kontrast: co to zmienia vs poprzedni okres lub vs sektor
  • forward: co może się wydarzyć dalej (bez rekomendacji inwestycyjnych)
  NIE używaj tego samego stylu dla dwóch spółek w jednej nitce.

--- Tweet ostatni: CLOSING — JEDEN TWEET, NIE DWA ---
KRYTYCZNE: pytanie + bookmark + disclaimer — JEDEN element tablicy JSON, max 280 znaków.

Użyj dokładnie tego formatu (fraza_closing jest podana w wiadomości użytkownika):
"[fraza_closing] Napisz w komentarzu!\n\n💾 Zapisz na później\nNie jest to rekomendacja inwestycyjna. #GPW #ESPI #SmallCaps"

Nie zmieniaj fraza_closing — wstaw ją dosłownie przed "Napisz w komentarzu!".

=== CZEGO UNIKAĆ ===
- Powtarzanie tej samej spółki w dwóch osobnych tweetach
- Frazy AI-style: "warto obserwować", "to fascynujące", "potencjalny wpływ", "warto śledzić"
- Placeholdery zamiast liczb
- Linki w tweetach
- Więcej niż JEDEN cashtag w POJEDYNCZYM tweecie (X odrzuca taki post — błąd 403);
  w hooku cashtag tylko przy spółce z "cashtag_spolki", w closingu żaden
- Ticker w nawiasie bez spacji, np. ($LBW) lub (WAS) — ZAWSZE ze spacjami
- Sugestie inwestycyjne (patrz zakaz powyżej)

=== FORMAT ODPOWIEDZI ===
Zwróć TYLKO JSON:
{"tweets": ["<tweet1>", ..., "<tweetN>"]}
Liczba elementów = 1 + liczba spółek + 1.
"""


class _PostResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tweets: list[str]


@dataclass
class GeneratedPost:
    tweets: list[str]


def _pick_variant(variants: list[str], salt: str = "") -> str:
    day = datetime.date.today().isoformat()
    idx = int(hashlib.md5(f"{day}{salt}".encode()).hexdigest(), 16)
    return variants[idx % len(variants)]


def _build_tickers_str(tickers: list[str]) -> str:
    # Closing lists several tickers in ONE tweet → all PLAIN TEXT, no $ cashtag (multiple
    # cashtags in a single tweet would be X error 403). Body tweets carry one cashtag each
    # (one company per tweet); the hook carries the top-score company's single cashtag.
    tagged = list(tickers)
    if len(tagged) == 1:
        return tagged[0]
    return ", ".join(tagged[:-1]) + f" czy {tagged[-1]}"


# A parenthesised ticker token: optional $cashtag + an UPPERCASE ticker (so years like
# "(2025)" are left alone). Normalised to a single space inside the parens: "( $LBW )".
_PAREN_TICKER_RE = re.compile(r"\(\s*(\$?[A-Z][A-Z0-9]{0,9})\s*\)")


def _normalize_ticker_spacing(text: str) -> str:
    """Force `( TICKER )` / `( $TICKER )` spacing — Gemini drops the spaces inconsistently."""
    return _PAREN_TICKER_RE.sub(r"( \1 )", text)


def _enforce_body_cashtag(tweet: str) -> str:
    """Ensure a single-company body tweet carries its `$cashtag`.

    Each body tweet is about ONE company, so its parenthesised ticker gets the `$`
    (one cashtag per tweet is within X's per-post limit). The LLM drops it inconsistently
    — same unreliability as ticker spacing — so we enforce it deterministically. No-op
    unless the tweet has exactly one distinct ticker token, which keeps it from touching
    the hook (top-company `$` only) or closing (several plain tickers) if ever misapplied.
    """
    distinct = {t.lstrip("$") for t in _PAREN_TICKER_RE.findall(tweet)}
    if len(distinct) != 1:
        return tweet
    return _PAREN_TICKER_RE.sub(lambda m: f"( ${m.group(1).lstrip('$')} )", tweet)


_DOMAIN_TLD_RE = re.compile(r"\b([\w-]+)\.(pl|com|net|org|info|io|co)\b", re.IGNORECASE)


def _strip_domain_suffix(text: str) -> str:
    """Strip a domain-like `.<tld>` suffix so X's link auto-detection can't linkify it."""
    return _DOMAIN_TLD_RE.sub(r"\1", text)


_HASHTAG_RE = re.compile(r"#\w+")
# Disclaimer clause: bounded by sentence/line boundaries so it doesn't swallow adjacent text.
_DISCLAIMER_RE = re.compile(r"[^\n.!?]*rekomendacj[^\n.!?]*[.!?]?", re.IGNORECASE)
_BOUNDARY_CHARS = ".!?\n"


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges (ticker parens, hashtags, disclaimer clause) `_enforce_length` avoids trimming."""
    spans = [m.span() for m in _PAREN_TICKER_RE.finditer(text)]
    spans += [m.span() for m in _HASHTAG_RE.finditer(text)]
    spans += [m.span() for m in _DISCLAIMER_RE.finditer(text) if m.group(0)]
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _free_text_gaps(text: str, protected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    gaps = []
    cursor = 0
    for start, end in protected:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < len(text):
        gaps.append((cursor, len(text)))
    return [g for g in gaps if g[1] > g[0]]


def _trim_gap(gap_text: str, keep_len: int) -> str:
    if keep_len <= 0:
        return ""
    window = gap_text[:keep_len]
    # Prefer cutting at the nearest preceding sentence/clause boundary over mid-word.
    for i in range(len(window) - 1, -1, -1):
        if window[i] in _BOUNDARY_CHARS:
            return gap_text[: i + 1].rstrip()
    # No sentence boundary in range — fall back to the nearest word boundary, dropping
    # the trailing partial word rather than truncating it mid-character.
    for i in range(len(window) - 1, -1, -1):
        if window[i].isspace():
            return gap_text[:i].rstrip()
    return window.rstrip()


def _strip_trailing_ellipsis(text: str) -> str:
    text = text.rstrip()
    if text.endswith("…"):
        text = text[:-1].rstrip()
    while text.endswith("..."):
        text = text[:-3].rstrip()
    return text


def _enforce_length(tweet: str, limit: int = 280) -> str:
    """Trim a tweet to `limit` chars; pure, idempotent, protects ticker/hashtag/disclaimer spans best-effort."""
    if len(tweet) <= limit:
        return tweet

    text = tweet
    while len(text) > limit:
        gaps = _free_text_gaps(text, _protected_spans(text))
        if not gaps:
            break
        gap_start, gap_end = max(gaps, key=lambda g: g[1] - g[0])
        gap_text = text[gap_start:gap_end]
        excess = len(text) - limit
        keep_len = max(len(gap_text) - excess, 0)
        trimmed = _trim_gap(gap_text, keep_len)
        if trimmed == gap_text:
            break
        # Trimming a prefix of the gap can leave it abutting the next protected span
        # with no separator (e.g. "...się" + "( $PEP )") — reinsert a single space.
        if trimmed and gap_end < len(text) and not trimmed[-1].isspace() and not text[gap_end].isspace():
            trimmed += " "
        text = text[:gap_start] + trimmed + text[gap_end:]

    if len(text) > limit:
        text = text[:limit]

    return _strip_trailing_ellipsis(text)


def generate_post(
    announcements: list[dict],
    window: str | None = None,
    previous_issues: list[str] | None = None,
) -> GeneratedPost | None:
    """Generate an X thread from a list of announcement dicts.

    Tweet count: 1 hook + 1 per company + 1 closing.
    window: "ranek" | "poludnie" | "wieczor" — controls hook phrase.
    previous_issues: supervisor errors from the prior attempt — appended to the prompt.
    Returns None on any failure — caller handles retry logic.
    """
    seen_tickers: set[str] = set()
    enriched = []
    for row in announcements:
        ticker = row.get("ticker") or ""
        if not ticker:
            logger.info("post_generator: skipping no-ticker row %s", row.get("announcement_id"))
            continue
        if ticker in seen_tickers:
            logger.info("post_generator: skipping duplicate ticker %s", ticker)
            continue
        seen_tickers.add(ticker)
        structured = {}
        raw = row.get("structured_analysis")
        if raw:
            try:
                structured = json5.loads(raw)
            except Exception:
                logger.warning(
                    "post_generator: failed to parse structured_analysis for %s",
                    row.get("announcement_id"),
                )
        enriched.append({
            "ticker": ticker,
            "company": row.get("company"),
            "event_type": row.get("event_type"),
            "key_numbers": structured.get("key_numbers", []),
            "summary_pl": structured.get("summary_pl", ""),
        })

    if not enriched:
        logger.warning("post_generator: no valid announcements to generate post from")
        return None

    n_companies = len(enriched)
    expected_tweets = n_companies + 2

    window_key = window or "ranek"
    hook_phrase = _pick_variant(
        _HOOK_VARIANTS.get(window_key, _HOOK_VARIANTS["ranek"]),
        salt=f"hook-{window_key}",
    )

    tickers_str = _build_tickers_str([row["ticker"] for row in enriched])
    closing_q = _pick_variant(_CLOSING_QUESTIONS, salt="closing").replace("{tickers}", tickers_str)

    feedback_block = ""
    if previous_issues:
        issues_str = "\n".join(f"- {issue}" for issue in previous_issues)
        feedback_block = (
            f"\n\n⚠️ POPRZEDNIA PRÓBA ODRZUCONA — popraw WSZYSTKIE poniższe błędy:\n"
            f"{issues_str}\n"
            f"Każdy tweet musi mieć maksymalnie 260 znaków (margines bezpieczeństwa poniżej "
            f"limitu 280). Jeśli tweet jest za długi, skróć WYŁĄCZNIE opis zdarzenia (tekst "
            f"po tickerze i liczbach) — nigdy ticker, emoji, hashtagi ani disclaimer."
        )

    # The thread's single cashtag goes to the highest-score company. enriched preserves
    # fetch_top_n_for_window's score-DESC order, so [0] is the top (tie → first listed).
    cashtag_ticker = enriched[0]["ticker"]

    user_message = (
        f"fraza_hooka: \"{hook_phrase}\"\n"
        f"fraza_closing: \"{closing_q}\"\n"
        f"cashtag_spolki: \"${cashtag_ticker}\"\n\n"
        f"Dane: {json.dumps(enriched, ensure_ascii=False)}\n\n"
        f"Wygeneruj wątek: DOKŁADNIE {expected_tweets} tweetów "
        f"(1 hook + {n_companies} spółek + 1 closing)."
        f"{feedback_block}"
    )

    try:
        client = get_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_message,
            config=genai.types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )
        data = json5.loads(response.text)
        parsed = _PostResponse.model_validate(data)
        if len(parsed.tweets) == 0:
            logger.warning("post_generator: empty tweets list in response")
            return None
        last_idx = len(parsed.tweets) - 1
        tweets = []
        for i, t in enumerate(parsed.tweets):
            t = _normalize_ticker_spacing(t)
            t = _strip_domain_suffix(t)
            # Body tweets (one company each) carry that company's single $cashtag; the hook
            # (top-company $ only) and closing (several plain tickers) are left to the prompt.
            # Enforce the cashtag before length so the trim accounts for the added char.
            if 0 < i < last_idx:
                t = _enforce_body_cashtag(t)
            tweets.append(_enforce_length(t))
        return GeneratedPost(tweets=tweets)
    except ValidationError:
        logger.warning("post_generator: response schema invalid", exc_info=True)
        return None
    except Exception:
        logger.warning("post_generator: Gemini call failed", exc_info=True)
        return None
