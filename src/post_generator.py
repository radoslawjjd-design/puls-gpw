"""Gemini-powered X thread generator for ESPI/EBI announcements."""
import json
import logging
from dataclasses import dataclass

import json5
import google.genai as genai

from src.gemini_client import get_client, GEMINI_MODEL

logger = logging.getLogger(__name__)

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

=== STRUKTURA WĄTKU — DYNAMICZNA LICZBA TWEETÓW ===
Liczba tweetów = 1 (hook) + liczba spółek + 1 (closing).
Dla 3 spółek = 5 tweetów. Dla 4 spółek = 6 tweetów. Trzymaj się tej liczby ściśle.

--- Tweet 1: HOOK ---
Zacznij od 🚨, potem "N ważnych ESPI z GPW – zerknij przed sesją:", potem lista spółek
z bulletami •, zakończ pytaniem. Przykład dla 3 spółek:

🚨 3 ważne ESPI z GPW – zerknij przed sesją:
• Lubawa ($LBW) bije rekordy przychodów i zysku
• Foothills ($FTL) podwyższenie kapitału
• Hub.Tech ($HUB) emisja za 34,7 mln PLN
Która spółka najbardziej Cię interesuje?

--- Tweety środkowe: JEDNA SPÓŁKA = JEDEN TWEET (140–180 znaków) ---
NIE dziel jednej spółki na dwa tweety.

Format — każda wartość liczbowa na OSOBNEJ LINII:
📊 Nazwa Spółki ($TICKER)
[kluczowa liczba 1]
[kluczowa liczba 2 jeśli jest]
[jedno zdanie kontekstu lub pytanie prowokujące]

Przykład:
📊 Lubawa ($LBW)
Przychody Q1: 136,96 mln PLN
Zysk netto: 23,65 mln PLN
Drugi kwartał rekordowy z rzędu. Organika czy duże kontrakty?

Zasady:
- Emoji 📊 zawsze na początku
- Pełna nazwa + ($TICKER) — zawsze oba
- Liczby z key_numbers na osobnych liniach — bez wymyślania
- Jedno krótkie pytanie na końcu

--- Tweet ostatni: CLOSING — JEDEN TWEET, NIE DWA ---
KRYTYCZNE: pytanie + bookmark + disclaimer — JEDEN element tablicy JSON, max 280 znaków.

Format:
"[pytanie z wszystkimi $TICKER] Napisz w komentarzu!\n\n💾 Zapisz na później\nNie jest to rekomendacja inwestycyjna. #GPW #ESPI #SmallCaps"

Przykład dla $LBW, $FTL, $HUB:
"$LBW, $FTL czy $HUB — który ruch robi na Tobie największe wrażenie? Napisz w komentarzu!\n\n💾 Zapisz na później\nNie jest to rekomendacja inwestycyjna. #GPW #ESPI #SmallCaps"

=== CZEGO UNIKAĆ ===
- Powtarzanie tej samej spółki w dwóch osobnych tweetach
- Frazy AI-style: "warto obserwować", "to fascynujące", "potencjalny wpływ", "warto śledzić"
- Placeholdery zamiast liczb
- Linki w tweetach, więcej niż 2 hashtagi w całym wątku
- Sugestie inwestycyjne (patrz zakaz powyżej)

=== FORMAT ODPOWIEDZI ===
Zwróć TYLKO JSON:
{"tweets": ["<tweet1>", ..., "<tweetN>"]}
Liczba elementów = 1 + liczba spółek + 1.
"""


@dataclass
class GeneratedPost:
    tweets: list[str]


def generate_post(announcements: list[dict]) -> GeneratedPost | None:
    """Generate an X thread from a list of announcement dicts.

    Tweet count: 1 hook + 1 per company + 1 closing.
    Returns None on any failure — caller handles retry logic.
    """
    seen_tickers: set[str] = set()
    enriched = []
    for row in announcements:
        ticker = row.get("ticker") or ""
        if ticker and ticker in seen_tickers:
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

    n_companies = len(enriched)
    expected_tweets = n_companies + 2
    user_message = (
        f"Dane: {json.dumps(enriched, ensure_ascii=False)}\n\n"
        f"Wygeneruj wątek: DOKŁADNIE {expected_tweets} tweetów "
        f"(1 hook + {n_companies} spółek + 1 closing)."
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
        tweets = data.get("tweets")
        if not isinstance(tweets, list) or len(tweets) == 0:
            logger.warning("post_generator: response missing 'tweets' list")
            return None
        return GeneratedPost(tweets=tweets)
    except Exception:
        logger.warning("post_generator: Gemini call failed", exc_info=True)
        return None
