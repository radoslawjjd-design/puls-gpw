"""Gemini-powered X thread generator for ESPI/EBI announcements."""
import datetime
import hashlib
import json
import logging
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

=== ZASADA CASHTAG ($) vs HASHTAG (#) — KRYTYCZNE, X ODRZUCA NARUSZENIA ===
X pozwala na MAKSYMALNIE JEDEN cashtag ($TICKER) w jednym tweecie. Tweet z dwoma lub
więcej cashtagami jest odrzucany przez X (błąd 403). Dlatego BEZWZGLĘDNIE:
- HOOK (tweet 1) i CLOSING (ostatni tweet): tickery spółek WYŁĄCZNIE jako hashtagi
  (#TICKER) — NIGDY $TICKER. Tu może być wiele hashtagów, ale ZERO cashtagów.
- Tweet środkowy (jedna spółka): DOKŁADNIE jeden cashtag $TICKER tej spółki i żadnego
  innego cashtaga.
Nie używaj $TICKER w żadnym tweecie poza tweetem danej spółki.

=== STRUKTURA WĄTKU — DYNAMICZNA LICZBA TWEETÓW ===
Liczba tweetów = 1 (hook) + liczba spółek + 1 (closing).
Dla 1 spółki = 3 tweetów. Dla 3 spółek = 5 tweetów. Dla 4 spółek = 6 tweetów.
Trzymaj się tej liczby ściśle — użytkownik poda dokładną liczbę w wiadomości.

--- Tweet 1: HOOK ---
Zacznij od 🚨, potem "N ważne/ważnych ESPI z GPW – [FRAZA OKNA]:", potem lista spółek
z bulletami •, zakończ pytaniem. FRAZA OKNA jest podana w wiadomości użytkownika (klucz "fraza_hooka").

Tickery w hooku jako HASHTAGI (#TICKER), nigdy $TICKER.

Przykład dla 1 spółki (DOKŁADNIE 3 tweety łącznie):
🚨 1 ważne ESPI z GPW – zerknij przed sesją:
• Ekobox ( #EBX ) podpisanie istotnej umowy
Warto się przyjrzeć?

Przykład dla 3 spółek (DOKŁADNIE 5 tweetów łącznie):
🚨 3 ważne ESPI z GPW – zerknij przed sesją:
• Lubawa ( #LBW ) bije rekordy przychodów i zysku
• Foothills ( #FTL ) podwyższenie kapitału
• Hub.Tech ( #HUB ) emisja za 34,7 mln PLN
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
- Pełna nazwa + ( $TICKER ) — zawsze oba
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
- Cashtag ($TICKER) gdziekolwiek poza tweetem danej spółki, lub >1 cashtag w jednym tweecie
  (patrz zasada cashtag/hashtag — X odrzuca takie posty)
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
    # Closing uses HASHTAGS (#TICKER), not cashtags — X rejects posts with >1 cashtag.
    tagged = [f"#{t}" for t in tickers]
    if len(tagged) == 1:
        return tagged[0]
    return ", ".join(tagged[:-1]) + f" czy {tagged[-1]}"


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
            f"Każdy tweet musi mieć ≤280 znaków. Skróć hook jeśli lista spółek jest długa."
        )

    user_message = (
        f"fraza_hooka: \"{hook_phrase}\"\n"
        f"fraza_closing: \"{closing_q}\"\n\n"
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
        return GeneratedPost(tweets=parsed.tweets)
    except ValidationError:
        logger.warning("post_generator: response schema invalid", exc_info=True)
        return None
    except Exception:
        logger.warning("post_generator: Gemini call failed", exc_info=True)
        return None
