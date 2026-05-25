"""
Watchlist agent — tygodniowa analiza spółek GPW.
Gemini typuje top 5 kandydatów inwestycyjnych spoza aktualnego portfela.
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)

from storage.bq_client import get_bq_client

# Limity truncation dla promptu Gemini
_MAX_WATCHLIST_CHARS = 90_000  # max znaków JSON analiz w prompcie
_MAX_WATCHLIST_ITEMS = 300     # max elementów po przycięciu

# ── Prompty ────────────────────────────────────────────────────────────────────
WATCHLIST_SYSTEM = """Jesteś doświadczonym analitykiem finansowym specjalizującym się w polskim rynku kapitałowym (GPW, NewConnect).
Twoim zadaniem jest wyłonienie 5 najciekawszych spółek inwestycyjnych tygodnia na podstawie ogłoszeń ESPI/EBI.
Szukasz spółek z realnym potencjałem wzrostu, nie spekulacji.
Zwracasz odpowiedź WYŁĄCZNIE jako poprawny JSON bez żadnego dodatkowego tekstu, komentarzy ani znaczników Markdown."""

WATCHLIST_TEMPLATE = """Analizujesz tygodniowe ogłoszenia ESPI/EBI z GPW ({date_from} — {date_to}).
Szukasz 5 najciekawszych kandydatów inwestycyjnych SPOZA aktualnego portfela inwestora.

PROFIL INWESTORA:
- Dostępny kapitał: {budget_pln} PLN (jednorazowy zakup lub podział na kilka pozycji)
- Portfel już posiadany: {portfolio_status}
- Wyklucz z rekomendacji: {portfolio_tickers}
- Przy budżecie {budget_pln} PLN preferuj spółki których cena akcji umożliwia zakup min. 1 lota (100 szt.) lub przynajmniej kilku akcji — unikaj spółek z ceną > {budget_pln} PLN/akcję

Liczba przeanalizowanych ogłoszeń: {total_announcements}
Spółki z ogłoszeniami w tym tygodniu: {total_companies}

Kryteria selekcji (w kolejności ważności):
1. Silne fundamenty potwierdzone ogłoszeniami (wyniki, kontrakty, wzrost przychodów)
2. Strategiczne wydarzenia (akwizycje wzmacniające pozycję, nowe rynki, przełomowe kontrakty)
3. Pozytywna dynamika sentymentu przy niskim ryzyku
4. Wiarygodność zarządu (dotrzymywanie prognoz, transparentność)
5. Unikaj: spółek z problemami płynnościowymi, ujemnym kapitałem, ostrzeżeniami audytorów

Zwróć JSON o dokładnie tej strukturze:
{{
  "tydzien_od": "{date_from}",
  "tydzien_do": "{date_to}",
  "liczba_ogloszen": {total_announcements},
  "liczba_spolek": {total_companies},
  "top_picks": [
    {{
      "ticker": "TICKER_GPW",
      "spolka": "Pełna nazwa spółki",
      "conviction": "WYSOKA/SREDNIA/NISKA",
      "sentiment_tygodnia": "pozytywny/neutralny/negatywny/mieszany",
      "liczba_ogloszen_tygodniu": 0,
      "kluczowe_wydarzenia": ["wydarzenie 1", "wydarzenie 2"],
      "dlaczego_warto": "2-3 zdania — konkretne powody dlaczego ta spółka jest ciekawa inwestycyjnie",
      "ryzyka": "1-2 zdania o głównych ryzykach",
      "horyzont": "krotkoterminowy/sredniookresowy/dlugoterminowy"
    }}
  ],
  "makro_kontekst": "1-2 zdania jak warunki makro wpływają na te wybory",
  "podsumowanie_tygodnia": "3-4 zdania ogólnego podsumowania tygodnia inwestycyjnego na GPW",
  "do_obserwacji": ["ticker1", "ticker2", "ticker3"]
}}

Gdzie "do_obserwacji" to dodatkowe 3 spółki które nie zmieściły się w top 5 ale warto śledzić.

OGŁOSZENIA DO ANALIZY:
{analyses_json}"""


# ── Vertex AI ─────────────────────────────────────────────────────────────────

def _get_vertex_client():
    from agents.vertex_client import get_gemini_client
    return get_gemini_client()


# ── Ładowanie analiz z Drive ───────────────────────────────────────────────────

def _load_weekly_analyses(
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Ładuje wszystkie analizy dla podanego zakresu dat z BQ."""
    from agents.summary_agent import _normalize_bq_analysis

    try:
        bq_rows = get_bq_client().load_analyses_for_period(
            date_from = date_from,
            date_to   = date_to,
            mode      = "both",
        )
        if bq_rows:
            analyses = [_normalize_bq_analysis(r) for r in bq_rows]
            logger.info(f"BQ: {len(analyses)} analiz ({date_from}–{date_to})")
            return analyses
    except Exception as e:
        logger.warning(f"BQ load error: {e}")
    return []


def save_watchlist_to_bq(watchlist: dict, date_to: date):
    """Zapisuje watchlistę do BigQuery."""
    try:
        get_bq_client().upsert_watchlist(watchlist)
        logger.info(f"Watchlista zapisana w BQ: {date_to}")
    except Exception as e:
        logger.error(f"Błąd zapisu watchlisty do BQ: {e}")


_WATCHLIST_FIELDS = [
    "spolka", "data", "temat", "sentiment", "waga",
    "kluczowe_fakty", "podsumowanie", "szanse", "ryzyka",
]


def _build_analyses_json(analyses: list[dict], max_chars: int = _MAX_WATCHLIST_CHARS) -> str:
    """Buduje kompaktowy JSON analiz do przekazania do Gemini."""
    from utils.analyses import build_truncated_analyses_json
    return build_truncated_analyses_json(
        analyses, _WATCHLIST_FIELDS, max_chars=max_chars, max_items=_MAX_WATCHLIST_ITEMS,
    )


# ── Główna funkcja ─────────────────────────────────────────────────────────────

def generate_watchlist(
    date_from: date,
    date_to: date,
    portfolio_companies: list[str],
    portfolio_bankier_names: dict[str, str],
    budget_pln: int = 1000,
) -> dict | None:
    """
    Generuje tygodniową watchlistę spółek GPW.
    Zwraca dict z top_picks lub None jeśli brak danych.
    """
    logger.info(f"Generuję watchlistę: {date_from} — {date_to}")

    # 1. Załaduj analizy z tygodnia
    analyses = _load_weekly_analyses(date_from, date_to)

    if not analyses:
        logger.warning("Brak analiz dla okresu watchlisty")
        return None

    logger.info(f"Załadowano {len(analyses)} analiz z {len(set(a.get('_company') for a in analyses))} spółek")

    # 2. Zbuduj prompt
    portfolio_tickers = ", ".join(
        f"{ticker} ({name})"
        for ticker, name in sorted(portfolio_bankier_names.items())
    ) or "brak (portfel pusty)"

    portfolio_status = (
        f"{len(portfolio_bankier_names)} spółek: {portfolio_tickers}"
        if portfolio_bankier_names
        else "PUSTY — inwestor dopiero zaczyna, szuka pierwszych zakupów"
    )

    total_companies = len(set(a.get("_company", "?") for a in analyses))
    analyses_json   = _build_analyses_json(analyses)

    prompt = WATCHLIST_SYSTEM + "\n\n" + WATCHLIST_TEMPLATE.format(
        date_from           = str(date_from),
        date_to             = str(date_to),
        portfolio_tickers   = portfolio_tickers,
        portfolio_status    = portfolio_status,
        budget_pln          = budget_pln,
        total_announcements = len(analyses),
        total_companies     = total_companies,
        analyses_json       = analyses_json,
    )

    # 3. Wywołaj Gemini
    try:
        from agents.vertex_client import call_gemini_json
        watchlist = call_gemini_json(
            prompt,
            max_retries=2,
            metadata={
                "agent":           "watchlist",
                "date_from":       str(date_from),
                "date_to":         str(date_to),
                "analyses_count":  len(analyses),
                "total_companies": total_companies,
                "budget_pln":      budget_pln,
            },
            # Phase 4 re-enabled 2026-04-23: thinking_budget=768 (50% bufor vs 512).
            # Tygodniowy picker spółek — wymaga porównania i rankingu.
            thinking_budget=768,
            # PR#11 #5 fix: cap z 65535 → 8192. Watchlist visible ~2K + thinking
            # auto ~3K = bezpieczne. Eliminuje runaway przy ambiguous prompt.
            max_output_tokens=8192,
        )
        if watchlist:
            logger.info(f"Watchlista wygenerowana: {len(watchlist.get('top_picks', []))} picks")
        else:
            logger.error("Nie udało się wygenerować watchlisty")
        return watchlist

    except Exception as e:
        logger.error(f"Błąd Gemini: {e}")
        return None
