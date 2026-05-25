"""
Broker agent — tygodniowy zarządzający portfelem inwestycyjnym.

Czyta z BigQuery:
  - broker_portfolio (realne pozycje + gotówka)
  - analyses (analizy tygodniowe spółek)
  - prices (kursy OHLCV)
  - macro_data (kontekst makro)
  - broker_reports (poprzedni raport — ciągłość)
  - watchlist (tygodniowe picks — cross-reference)

Rekomenduje:
  - BUY:  do 5 nowych pozycji z alokacją PLN
  - HOLD/SELL: ocena każdej aktualnie posiadanej pozycji
  - Ogólna strategia na nadchodzący tydzień
"""
import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from agents.vertex_client import call_gemini_json
from storage.bq_client import get_bq_client
from utils.timezone import today_warsaw


def _is_missing_value(value) -> bool:
    """
    Detect placeholder values from Bankier scraper indicating MISSING data.

    Bankier zapisuje placeholders gdy nie znajdzie wartości na stronie:
      - "0,00" (string z polskim przecinkiem) — najczęściej dla wskaźników
      - "0.00", "0", 0, 0.0 — różne warianty zera
      - "-- --", "--", "-", "- -" — znaczniki braku w UI
      - None, "" — brak field-a

    Faktyczne zero (np. zysk_netto = 0,01 PLN) jest zwykle podawane jako "0,01".
    Czyste "0" / "0,00" zawsze oznacza brak danych w kontekście wskaźników.
    """
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    # Placeholders dash-based
    if s in ("--", "-- --", "-", "- -", "n/d", "N/D", "n.d.", "brak"):
        return True
    # Zera (placeholder)
    if s in ("0", "0,00", "0.00", "0,0", "0.0"):
        return True
    return False


def _format_financial_value(value) -> str:
    """Format wartość dla promptu: missing → 'brak danych', real → str(value)."""
    if _is_missing_value(value):
        return "brak danych"
    return str(value)


def _normalize_watch_list(items: list | None) -> list[str]:
    """
    Normalizuje `do_obserwacji` / `watch_list` do listy stringów (tickerów).

    Spec w prompcie wymaga `["ticker1", "ticker2"]`, ale Gemini czasem
    halucynuje i zwraca list of dicts (np. `[{"ticker": "LWB", "reason": "..."}]`).
    BQ schema wymaga `STRING (REPEATED)` — dict crashuje load.
    Plus broker.py:116 `', '.join(...)` crashuje na dictach.

    Defensywne: akceptuj listę stringów lub dictów (z polem 'ticker'),
    zwróć listę stringów. Skip items bez 'ticker' (loguj).
    """
    if not items:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            ticker = item.get("ticker")
            if ticker and isinstance(ticker, str):
                out.append(ticker)
            # else: skip silently (no ticker field)
    return out

logger = logging.getLogger(__name__)

# ── Statyczna polityka brokera (broker/strategy.md + broker/rules.md) ────────
# Pliki baked do obrazu Docker przez Dockerfile (selective un-ignore w
# .dockerignore). Czytane raz per process, cached — są immutable w runtime.
_POLICY_CACHE: dict[str, dict] = {}

# ── Conviction filter ────────────────────────────────────────────────────────

_MIN_CONVICTION = {"SREDNIA", "WYSOKA"}


def filter_by_conviction(recommendations: list[dict]) -> list[dict]:
    """Zachowaj tylko rekomendacje z conviction >= SREDNIA."""
    return [r for r in recommendations
            if r.get("conviction", "").upper() in _MIN_CONVICTION]


def _cap_recommendations_to_budget(
    report: dict,
    gotowka_pln: float,
    buffer_pct: float = 0.10,
) -> dict:
    """PR#12 CRITICAL #2: ogranicz sumę rekomendowanych zakupów do dostępnej gotówki.

    Gemini halucynuje liczbami i może zarekomendować zakupy za >100% kapitału.
    Bez tego sanity-check admin ufnie kupiłby nadmiar.

    Strategia:
    1. Sortuj rekomendacje po conviction (WYSOKA > SREDNIA > NISKA) i kwocie desc.
    2. Dodawaj jedna po drugiej dopóki suma ≤ gotowka × (1 - buffer_pct).
    3. Odrzuć resztę z log warningiem.

    buffer_pct=0.10 → max 90% gotówki, 10% rezerwy na slippage / commission.
    Mutuje `report` in-place.
    """
    if gotowka_pln <= 0:
        # Brak gotówki — odrzuć wszystkie zakupy
        for key in ("rekomendacje_zakupu", "rekomendacje_krotkoterminowe"):
            if report.get(key):
                logger.warning(
                    f"Budget cap: gotowka_pln={gotowka_pln} ≤ 0 → "
                    f"odrzuca {len(report[key])} z {key}"
                )
                report[key] = []
        return report

    max_budget = gotowka_pln * (1.0 - buffer_pct)
    _CONVICTION_RANK = {"WYSOKA": 3, "SREDNIA": 2, "NISKA": 1, "": 0}

    for key in ("rekomendacje_zakupu", "rekomendacje_krotkoterminowe"):
        items = report.get(key) or []
        if not items:
            continue

        # Sortuj: conviction desc, kwota desc
        sorted_items = sorted(
            items,
            key=lambda r: (
                _CONVICTION_RANK.get((r.get("conviction") or "").upper(), 0),
                float(r.get("kwota_pln") or 0),
            ),
            reverse=True,
        )

        kept: list[dict] = []
        running = 0.0
        rejected_for_budget: list[str] = []
        for r in sorted_items:
            kwota = float(r.get("kwota_pln") or 0)
            if kwota <= 0:
                rejected_for_budget.append(f"{r.get('ticker')}(kwota={kwota})")
                continue
            if running + kwota <= max_budget:
                kept.append(r)
                running += kwota
            else:
                rejected_for_budget.append(f"{r.get('ticker')}({kwota:.0f}zł)")

        if rejected_for_budget:
            logger.warning(
                f"Budget cap {key}: gotowka={gotowka_pln:.0f} PLN, "
                f"max_budget={max_budget:.0f} PLN ({(1-buffer_pct)*100:.0f}%), "
                f"przyjęto={running:.0f} PLN ({len(kept)} pozycji), "
                f"odrzucono={rejected_for_budget}"
            )
        report[key] = kept

    return report


def _filter_dd_to_candidates(report: dict, candidate_tickers: list[str]) -> dict:
    """PR#12 CRITICAL #1: odfiltruj rekomendacje zakupu z DD output do tickerów,
    które przeszły screening (Etap 1).

    Gemini DD prompt explicitly mówi "NIE DODAWAJ nowych spółek spoza listy
    kandydatów" — ale Gemini halucynuje. Bez post-hoc filtru admin może kupić
    spółkę bez DD.

    Mutuje `report` in-place, zwraca też dla wygody. Loguje WARNING dla każdej
    odrzuconej halucynacji.
    """
    allowed = {t.upper() for t in (candidate_tickers or []) if t}
    for key in ("rekomendacje_zakupu", "rekomendacje_krotkoterminowe"):
        original = report.get(key, []) or []
        filtered = [r for r in original
                    if (r.get("ticker") or "").upper() in allowed]
        rejected = [r.get("ticker") for r in original
                    if (r.get("ticker") or "").upper() not in allowed]
        if rejected:
            logger.warning(
                f"DD halucynacja: odrzucono {len(rejected)} z {key}: {rejected} "
                f"(spoza screening candidate_tickers={list(allowed)[:10]})"
            )
        report[key] = filtered
    return report


def _load_broker_policy(short: bool = False) -> dict:
    """Czyta broker/strategy.md + broker/rules.md jako statyczną politykę.

    short=True → broker/strategy_short.md + broker/rules_short.md.
    Cached per mode — pliki są immutable w runtime (baked do Docker image).
    Graceful fallback — jeśli plik nie istnieje lub read się nie uda,
    zwraca pusty string dla danego klucza (broker działa bez polityki).
    """
    cache_key = "short" if short else "standard"
    if cache_key in _POLICY_CACHE:
        return _POLICY_CACHE[cache_key]

    suffix = "_short" if short else ""
    result = {"strategy": "", "rules": ""}
    for key, path in [
        ("strategy", f"broker/strategy{suffix}.md"),
        ("rules",    f"broker/rules{suffix}.md"),
    ]:
        try:
            p = Path(path)
            if p.exists():
                result[key] = p.read_text(encoding="utf-8")
                logger.info(f"Policy loaded: {path} ({len(result[key])} chars)")
            else:
                logger.warning(f"Policy file not found: {path}")
        except Exception as e:
            logger.warning(f"Błąd czytania {path}: {e}")

    _POLICY_CACHE[cache_key] = result
    return result

# ── Domyślny pusty portfel ────────────────────────────────────────────────────
_EMPTY_PORTFOLIO = {
    "gotowka_pln": 1000.0,
    "pozycje":     [],
}

# ── Prompt ────────────────────────────────────────────────────────────────────

_BROKER_SYSTEM = """Jesteś doświadczonym zarządcą portfela inwestycyjnego na GPW (Giełda Papierów Wartościowych w Warszawie).
Zarządzasz realnym portfelem inwestora indywidualnego o konserwatywno-umiarkowanym profilu ryzyka.
Twoje decyzje muszą być oparte WYŁĄCZNIE na dostarczonych danych analitycznych — nie wymyślaj faktów.
Odpowiadasz WYŁĄCZNIE poprawnym JSON bez żadnych dodatkowych komentarzy ani znaczników Markdown."""

_BROKER_TEMPLATE = """Przeprowadź tygodniowy przegląd portfela inwestycyjnego na podstawie ogłoszeń ESPI/EBI z GPW ({date_from} — {date_to}).

═══════════════════════════════════════
AKTUALNY STAN PORTFELA
═══════════════════════════════════════
Dostępna gotówka: {gotowka_pln} PLN
Łączna wartość portfela (szacunkowa): {wartosc_portfela_pln} PLN

Posiadane pozycje:
{pozycje_str}

═══════════════════════════════════════
KURSY AKCJI — ostatnie 7 dni
Format: ticker  O=otwarcie_pon  C=zamknięcie_pt  tydz.zmiana  sesji
═══════════════════════════════════════
{prices_str}

═══════════════════════════════════════
PODSUMOWANIA TYGODNIOWE SPÓŁEK (ESPI/EBI)
Zagregowane per spółka z {total_announcements} ogłoszeń ({total_companies} spółek)
═══════════════════════════════════════
{analyses_json}

{extra_context}
═══════════════════════════════════════
ZADANIE
═══════════════════════════════════════
1. OCEN każdą posiadaną pozycję: TRZYMAJ / OBSERWUJ / SPRZEDAJ
2. ZAPROPONUJ nowe zakupy (max 5):
   - Twój EFEKTYWNY BUDŻET = {gotowka_pln} PLN + suma wartości rynkowych pozycji
     które rekomendujesz SPRZEDAJ (po kursie zamknięcia z sekcji "Aktualne kursy")
   - Przykład: gotówka 30 PLN + SPRZEDAJ pozycji wartej 250 PLN = budżet 280 PLN
   - Suma rekomendowanych zakupów ≤ efektywny_budżet × 0.9 (10% bufor bezpieczeństwa)
   - Podaj konkretną kwotę w PLN dla każdego zakupu
   - Preferuj spółki z ceną akcji dostępną przy danym budżecie
   - JEŚLI rekomendujesz SPRZEDAJ — MUSISZ rozważyć propozycję BUY za uwolniony kapitał
     (chyba że sentyment rynku ekstremalnie negatywny — wtedy uzasadnij to w komentarzu)
3. OCEŃ ogólną sytuację rynkową na podstawie tygodnia

⚠️ KRYTYCZNE — INTERPRETACJA DANYCH FINANSOWYCH:
Wartość "brak danych" w polu P/E, zysk netto, EV/EBITDA itp. oznacza że
Bankier scraper NIE znalazł wartości na stronie. NIE oznacza zera, straty
ani złych fundamentów. NIE używaj "brak danych" jako uzasadnienia SPRZEDAJ.
Bazuj na: rekomendacjach analityków, dywidendach, kapitalizacji, sentyment
ogłoszeń, sektorze, kontekście makro. Brak P/E = brak sygnału, nie sygnał
negatywny.

Kryteria selekcji nowych zakupów:
- Silne fundamenty potwierdzone ogłoszeniami (wyniki, kontrakty, wzrost)
- Pozytywny lub mieszany sentyment tygodnia
- Niska/umiarkowana waga ryzyka
- Realna szansa na wzrost w horyzoncie 3-12 miesięcy
- Unikaj spółek z problemami płynnościowymi lub ostrzeżeniami audytorów

Zwróć JSON o DOKŁADNIE tej strukturze:
{{
  "data_raportu": "{date_to}",
  "tydzien_od": "{date_from}",
  "tydzien_do": "{date_to}",
  "gotowka_dostepna_pln": {gotowka_pln},
  "ocena_portfela": [
    {{
      "ticker": "TICKER",
      "spolka": "Nazwa spółki",
      "rekomendacja": "TRZYMAJ",
      "uzasadnienie": "1-2 zdania dlaczego",
      "zmiana_sentymentu": "poprawa/bez_zmian/pogorszenie",
      "alerty": []
    }}
  ],
  "rekomendacje_zakupu": [
    {{
      "ticker": "TICKER",
      "spolka": "Nazwa spółki",
      "kwota_pln": 200,
      "conviction": "WYSOKA/SREDNIA/NISKA",
      "uzasadnienie": "2-3 zdania — konkretne powody",
      "ryzyka": "1-2 zdania",
      "horyzont": "krotkoterminowy/sredniookresowy/dlugoterminowy",
      "liczba_ogloszen_tygodniu": 0
    }}
  ],
  "rekomendacje_krotkoterminowe": [
    {{
      "ticker": "TICKER",
      "spolka": "Nazwa spółki",
      "kwota_pln": 150,
      "conviction": "WYSOKA/SREDNIA/NISKA",
      "uzasadnienie": "2-3 zdania — dlaczego teraz i dlaczego krótkoterminowo",
      "ryzyka": "1-2 zdania",
      "katalizator": "Konkretne nadchodzące zdarzenie (wyniki, dywidenda, kontrakt)",
      "horyzont_dni": 14,
      "liczba_ogloszen_tygodniu": 0
    }}
  ],
  "rozliczenie_poprzednich": [
    {{
      "ticker": "TICKER",
      "poprzednia_rekomendacja": "KUPUJ 200 PLN / TRZYMAJ / OBSERWUJ",
      "decyzja": "utrzymuję/zmieniam/usuwam",
      "powod": "1 zdanie — dlaczego utrzymujesz lub zmieniasz (KONKRETNE fakty z tego tygodnia)"
    }}
  ],
  "gotowka_po_zakupach_pln": 0,
  "sentyment_rynku": "pozytywny/neutralny/negatywny/mieszany",
  "komentarz_tygodnia": "3-4 zdania ogólnej oceny tygodnia i warunków rynkowych",
  "do_obserwacji": ["ticker1", "ticker2", "ticker3"],
  "wiki_updates": {{
    "positions": [
      {{
        "ticker": "TICKER",
        "current_thesis_new": "2-4 zdania — zaktualizowana teza: co się zmieniło od wejścia/poprzedniego tygodnia",
        "weekly_check_in": {{
          "date": "{date_to}",
          "sentiment": "poprawa/bez_zmian/pogorszenie",
          "notes": "1-2 zdania KONKRETNE fakty z tego tygodnia"
        }},
        "catalysts_positive_add": ["nowy katalizator pozytywny"],
        "catalysts_negative_add": ["nowe ryzyko"],
        "target_price_new": 0
      }}
    ],
    "lessons_added": [
      {{
        "ticker": "TICKER lub null",
        "category": "entry/exit/sizing/sector/macro",
        "what_happened": "2-3 zdania — co się wydarzyło",
        "what_learned": "1-2 zdania — insight",
        "rule_derived": "uogólniona reguła lub null"
      }}
    ]
  }}
}}

Definicje horyzontów:
- krotkoterminowy: do 1 miesiąca (szybkie okazje, nadchodzące zdarzenia, momentum cenowy)
- sredniookresowy: 1-6 miesięcy (fundamenty, wzrost przychodów, nowe kontrakty)
- dlugoterminowy: 6-12 miesięcy (transformacja biznesowa, ekspansja, zmiana sektora)

Gdzie:
- "ocena_portfela" zawiera wpisy dla KAŻDEJ posiadanej pozycji (może być pusta lista)
- "rekomendacja" to jedno z: TRZYMAJ / OBSERWUJ / SPRZEDAJ
- "alerty" to lista ostrzeżeń jeśli wykryto coś niepokojącego (może być pusta)
- "rekomendacje_zakupu" to max 5 spółek SPOZA aktualnego portfela (horyzont średnio/długoterminowy)
- "rekomendacje_krotkoterminowe" to max 3 spółki z katalizatorem w ciągu 1-30 dni (mogą się powtarzać z rekomendacje_zakupu jeśli mają też krótkoterminowy potencjał)
- "katalizator" to KONKRETNE nadchodzące zdarzenie: wyniki kwartalne, odcięcie dywidendy, wejście do indeksu, kontrakt, etc.
- "horyzont_dni" to szacowana liczba dni do realizacji katalizatora (1-30)
- "rozliczenie_poprzednich" — dla KAŻDEJ spółki z poprzednich rekomendacji (zakupy + oceny portfela):
  - "utrzymuję" = spółka nadal rekomendowana, brak negatywnych zmian
  - "zmieniam" = zmieniam rekomendację (np. z KUPUJ na OBSERWUJ) — wymagane KONKRETNE uzasadnienie
  - "usuwam" = całkowicie usuwam z rekomendacji — wymagane KONKRETNE uzasadnienie
  Jeśli nie było poprzednich rekomendacji, zwróć pustą listę []
- "gotowka_po_zakupach_pln" = {gotowka_pln} + przychod ze SPRZEDAJ (po kursie zamkniecia) - suma kwota_pln ze WSZYSTKICH zakupów (obu list)
- "wiki_updates.positions" — WYPEŁNIAJ TYLKO dla pozycji gdzie coś istotnego się zmieniło od poprzedniego tygodnia. Nie wypisuj wszystkich pozycji jeśli nic się nie zmieniło — to OPCJONALNE updates, nie rozliczenie. Sekcja WIKI PORTFELA powyżej pokazuje Ci aktualną tezę każdej pozycji — aktualizuj ją TYLKO gdy masz KONKRETNE nowe fakty z tego tygodnia. weekly_check_in może być wypełnione nawet przy bez_zmian — to tygodniowy marker że zobaczyłeś pozycję.
- "wiki_updates.lessons_added" — WYPEŁNIAJ TYLKO gdy wyciągasz KONKRETNĄ lekcję z obserwacji/decyzji. Nie wymuszaj generowania lessons co tydzień. Jeśli brak nowych lekcji, zwróć pustą listę []. Lessons powinny być uogólniane (np. "spadek >10% bez zmiany tezy → SELL" zamiast "XYZ spadło")."""


# ── Portfel z BQ ─────────────────────────────────────────────────────────────

def load_portfolio_from_bq(short: bool = False) -> dict:
    """
    Wczytuje portfel z BQ (broker_portfolio, ostatni snapshot).
    Konwertuje format BQ → wewnętrzny format brokera.
    Jeśli brak snapshotu — zwraca domyślny (1000 PLN, 0 pozycji).
    """
    snapshot = get_bq_client().load_portfolio(short=short)

    if not snapshot:
        logger.info("Brak snapshotu portfela w BQ — używam domyślnego (1000 PLN)")
        return _EMPTY_PORTFOLIO.copy()

    positions = snapshot.get("positions") or []
    pozycje = []
    for p in positions:
        pozycje.append({
            "ticker":             p.get("ticker", ""),
            "spolka":             p.get("company", ""),
            "liczba_akcji":       p.get("shares", 0),
            "srednia_cena_zakupu": p.get("avg_price", 0.0),
            "data_zakupu":        p.get("buy_date", ""),
        })

    portfolio = {
        "gotowka_pln": snapshot.get("cash_pln", 0.0),
        "pozycje":     pozycje,
    }
    logger.info(
        f"Portfel z BQ: {len(pozycje)} pozycji, "
        f"{portfolio['gotowka_pln']:.0f} PLN gotówki"
    )
    return portfolio


def save_portfolio_to_bq(portfolio: dict, snapshot_date: date | None = None,
                         short: bool = False):
    """Zapisuje snapshot portfela do BQ."""
    sd = snapshot_date or today_warsaw()
    positions = []
    for p in portfolio.get("pozycje", []):
        positions.append({
            "ticker":    p.get("ticker", ""),
            "company":   p.get("spolka", ""),
            "shares":    p.get("liczba_akcji", 0),
            "avg_price": p.get("srednia_cena_zakupu", 0.0),
            "buy_date":  p.get("data_zakupu", ""),
        })

    total = portfolio.get("gotowka_pln", 0.0)
    for p in portfolio.get("pozycje", []):
        total += p.get("liczba_akcji", 0) * p.get("srednia_cena_zakupu", 0)

    get_bq_client().upsert_portfolio({
        "snapshot_date":   sd,
        "cash_pln":        portfolio.get("gotowka_pln", 0.0),
        "total_value_pln": total,
        "positions":       positions,
    }, short=short)
    logger.info(f"Portfel {'short ' if short else ''}zapisany w BQ: snapshot {sd}")


def save_broker_report_to_bq(report: dict, week_from: date, week_to: date,
                             short: bool = False):
    """Zapisuje raport brokera do BQ."""
    try:
        get_bq_client().upsert_broker_report(report, week_from, week_to, short=short)
        label = "short " if short else ""
        logger.info(f"Raport brokera {label}zapisany w BQ: {week_from}–{week_to}")
    except Exception as e:
        logger.error(f"Błąd zapisu raportu brokera do BQ: {e}")


# ── Ładowanie danych z BQ ────────────────────────────────────────────────────

def _load_weekly_prices_from_bq(
    date_from: date,
    date_to: date,
) -> dict[str, list[dict]]:
    """
    Ładuje kursy z BQ i reshapuje do {ticker: [{date, open, high, low, close, volume}]}.
    """
    rows = get_bq_client().load_prices_for_period(date_from, date_to)
    result: dict[str, list[dict]] = {}
    for r in rows:
        ticker = r.get("ticker", "")
        if ticker not in result:
            result[ticker] = []
        result[ticker].append({
            "date":   str(r.get("price_date", "")),
            "open":   r.get("open"),
            "high":   r.get("high"),
            "low":    r.get("low"),
            "close":  r.get("close"),
            "volume": r.get("volume"),
        })
    logger.info(f"Kursy z BQ: {len(result)} tickerów ({date_from}–{date_to})")
    return result


def _load_weekly_analyses(date_from: date, date_to: date) -> list[dict]:
    """Ładuje analizy tygodnia z BQ, normalizuje do formatu brokera."""
    from agents.summary_agent import _normalize_bq_analysis

    try:
        bq_rows = get_bq_client().load_analyses_for_period(
            date_from=date_from, date_to=date_to, mode="both",
        )
        if bq_rows:
            analyses = [_normalize_bq_analysis(r) for r in bq_rows]
            # Dodaj _company (alias używany przez _aggregate_analyses_by_company)
            for a in analyses:
                a["_company"] = a.get("spolka", "UNKNOWN")
            logger.info(f"BQ: {len(analyses)} analiz ({date_from}–{date_to})")
            return analyses
    except Exception as e:
        logger.warning(f"BQ load analyses error: {e}")
    return []


def _load_previous_report(target_date: date, short: bool = False) -> dict | None:
    """Ładuje poprzedni raport brokera z BQ."""
    try:
        return get_bq_client().load_broker_report(target_date, short=short)
    except Exception as e:
        logger.warning(f"Nie udało się załadować poprzedniego raportu: {e}")
        return None


def _build_company_enrichment() -> dict[str, dict]:
    """Buduje {nazwa_pelna: {ticker, sektor, makrosektor}} z company_profiles.

    Używane do:
    - Mapowania nazwa spółki → ticker (dla yfinance price fetch, Luka 1)
    - Wzbogacenia analyses o sektor (Luka 3)
    - Wzbogacenia positions wiki o sektor (Luka 3)

    Graceful fallback: zwraca {} gdy BQ niedostępne — broker działa bez enrichment.
    """
    try:
        profiles = get_bq_client().load_profiles()
    except Exception as e:
        logger.warning(f"Nie udało się załadować company_profiles: {e}")
        return {}

    if not profiles:
        return {}

    result: dict[str, dict] = {}
    for ticker, profile in profiles.items():
        nazwa = profile.get("nazwa_pelna") or ticker
        if not nazwa:
            continue
        entry = {
            "ticker":      ticker,
            "sektor":      profile.get("sektor", ""),
            "makrosektor": profile.get("makrosektor", ""),
        }
        # Pełny profil (jeśli create_profile z Gemini był uruchomiony)
        for key in ("model_biznesowy", "glowne_produkty_uslugi",
                     "katalizatory_wzrostu", "ryzyka_makroekonomiczne",
                     "polityka_dywidendowa", "charakterystyka_dla_analizy"):
            val = profile.get(key)
            if val:
                entry[key] = val
        # Surowe dane z Bankier (wskaźniki, rekomendacje, dywidendy)
        raw = profile.get("_bankier_raw") or profile.get("bankier_raw_json") or {}
        if raw and isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = {}
        if raw:
            entry["_bankier_raw"] = raw

        result[nazwa] = entry

    logger.info(f"Company enrichment: {len(result)} spółek (ticker+sektor+profil)")
    return result


def _fetch_prices_for_analyzed_companies(
    analyses: list[dict],
    enrichment: dict[str, dict],
    existing_prices: dict[str, list[dict]],
    date_from: date,
    date_to: date,
) -> dict[str, list[dict]]:
    """Dociąga OHLCV przez yfinance dla spółek z analiz których brak w BQ prices.

    Bez tego fixa broker widzi ceny tylko ~60 spółek (portfel + WIG20 + mWIG40)
    i rekomenduje "w ciemno" dla ~339 pozostałych z ogłoszeniami.

    Merge strategy: existing_prices priority (BQ), yfinance uzupełnia brakujące.
    Graceful fallback: zwraca existing_prices gdy yfinance failuje.
    """
    if not analyses:
        return existing_prices

    # Unique tickers z analyses (via enrichment lookup)
    tickers_to_fetch: list[str] = []
    seen: set[str] = set()
    for a in analyses:
        company = a.get("_company", "")
        if not company or company in seen:
            continue
        seen.add(company)

        entry = enrichment.get(company)
        if not entry:
            continue
        ticker = entry.get("ticker", "")
        if not ticker:
            continue
        if ticker in existing_prices:
            continue
        if ticker in tickers_to_fetch:
            continue
        tickers_to_fetch.append(ticker)

    if not tickers_to_fetch:
        return existing_prices

    logger.info(
        f"yfinance fetch: {len(tickers_to_fetch)} tickerów brakujących w BQ prices "
        f"({', '.join(tickers_to_fetch[:5])}{'...' if len(tickers_to_fetch) > 5 else ''})"
    )

    try:
        from scraper.prices import fetch_ohlcv_batch
        fetched = fetch_ohlcv_batch(tickers_to_fetch, date_from, date_to)
    except Exception as e:
        logger.warning(f"yfinance fetch failed: {e} — fallback to existing prices")
        return existing_prices

    merged = dict(existing_prices)
    merged.update(fetched)
    logger.info(
        f"Prices enriched: {len(existing_prices)} z BQ + {len(fetched)} z yfinance "
        f"= {len(merged)} total"
    )
    return merged


def _load_positions_wiki_for_context(
    enrichment: dict[str, dict] | None = None,
    short: bool = False,
) -> str:
    """Ładuje aktywne pozycje wiki z BQ + formatuje jako JSON dla promptu.

    Trimuje weekly_check_ins do ostatnich 3 per pozycja (kontekst wystarczający,
    oszczędność tokenów). Gdy `enrichment` podany (Luka 3 fix) — dopisuje
    `sektor` i `makrosektor` per pozycja z `company_profiles`. Zwraca pusty
    string gdy brak aktywnych pozycji.
    """
    try:
        positions = get_bq_client().load_active_positions_wiki(short=short)
    except Exception as e:
        logger.warning(f"Nie udało się załadować positions_wiki: {e}")
        return ""

    if not positions:
        return ""

    trimmed = []
    for p in positions:
        check_ins = p.get("weekly_check_ins") or []
        # Ostatnie 3 check-iny
        if len(check_ins) > 3:
            check_ins = list(check_ins)[-3:]

        spolka = p.get("spolka", "")
        entry = {
            "ticker":             p.get("ticker", ""),
            "spolka":             spolka,
            "entry_date":         str(p.get("entry_date", "")),
            "entry_price":        p.get("entry_price"),
            "entry_thesis":       p.get("entry_thesis", ""),
            "current_thesis":     p.get("current_thesis", ""),
            "target_price":       p.get("target_price"),
            "horizon":            p.get("horizon", ""),
            "catalysts_positive": list(p.get("catalysts_positive") or []),
            "catalysts_negative": list(p.get("catalysts_negative") or []),
            "weekly_check_ins":   [dict(c) for c in check_ins],
        }

        # Sector enrichment (Luka 3)
        if enrichment:
            info = enrichment.get(spolka) or {}
            entry["sektor"]      = info.get("sektor", "")
            entry["makrosektor"] = info.get("makrosektor", "")

        trimmed.append(entry)

    return json.dumps(trimmed, ensure_ascii=False, indent=2, default=str)


def _load_recent_lessons_for_context(short: bool = False) -> str:
    """Ładuje ostatnie 20 lekcji z BQ + formatuje jako JSON dla promptu.

    Zwraca pusty string gdy brak lekcji.
    """
    try:
        lessons = get_bq_client().load_recent_lessons(limit=20, short=short)
    except Exception as e:
        logger.warning(f"Nie udało się załadować recent_lessons: {e}")
        return ""

    if not lessons:
        return ""

    trimmed = []
    for lesson in lessons:
        trimmed.append({
            "lesson_date":   str(lesson.get("lesson_date", "")),
            "ticker":        lesson.get("ticker"),
            "category":      lesson.get("category", ""),
            "what_happened": lesson.get("what_happened", ""),
            "what_learned":  lesson.get("what_learned", ""),
            "rule_derived":  lesson.get("rule_derived"),
        })

    return json.dumps(trimmed, ensure_ascii=False, indent=2, default=str)


# ── Wskaźniki techniczne ────────────────────────────────────────────────────

def _calc_technical_indicators(
    prices: dict[str, list[dict]],
) -> dict[str, dict]:
    """Oblicza wskaźniki techniczne z OHLCV.

    Per ticker returns:
      rsi14: float|None     — RSI(14) Wilder's smoothing
      sma20_pct: float|None — (close/SMA20 - 1) * 100
      chg_1m: float|None    — % zmiana vs ~20 sesji temu
      chg_3m: float|None    — % zmiana vs ~60 sesji temu
      chg_6m: float|None    — % zmiana vs ~120 sesji temu
      vol_avg20: float|None — średni wolumen 20 sesji
      high_52w: float|None  — max close z dostępnych danych
      low_52w: float|None   — min close z dostępnych danych
    """
    result: dict[str, dict] = {}
    for ticker, sessions in prices.items():
        closes: list[float] = [float(s["close"]) for s in sessions
                                if s.get("close") is not None]
        volumes: list[float] = [float(s["volume"]) for s in sessions
                                 if s.get("volume") is not None]

        entry: dict[str, float | None] = {
            "rsi14": None, "sma20_pct": None,
            "chg_1m": None, "chg_3m": None, "chg_6m": None,
            "vol_avg20": None, "high_52w": None, "low_52w": None,
        }

        if not closes:
            result[ticker] = entry
            continue

        # 52w high/low
        entry["high_52w"] = max(closes)
        entry["low_52w"] = min(closes)

        # RSI(14) — Wilder's smoothing
        if len(closes) >= 15:
            gains = []
            losses = []
            for i in range(1, len(closes)):
                delta = closes[i] - closes[i - 1]
                gains.append(max(delta, 0.0))
                losses.append(max(-delta, 0.0))

            # First average: SMA of first 14
            avg_gain = sum(gains[:14]) / 14
            avg_loss = sum(losses[:14]) / 14

            # Wilder's smoothing for remaining
            for i in range(14, len(gains)):
                avg_gain = (avg_gain * 13 + gains[i]) / 14
                avg_loss = (avg_loss * 13 + losses[i]) / 14

            if avg_loss == 0:
                entry["rsi14"] = 100.0 if avg_gain > 0 else 50.0
            else:
                rs = avg_gain / avg_loss
                entry["rsi14"] = round(100.0 - (100.0 / (1.0 + rs)), 1)

        # SMA(20) ratio
        if len(closes) >= 20:
            sma20 = sum(closes[-20:]) / 20
            if sma20 > 0:
                entry["sma20_pct"] = round((closes[-1] / sma20 - 1) * 100, 1)

        # Price changes (vs N sessions ago)
        current = closes[-1]
        if current > 0:
            if len(closes) >= 20:
                entry["chg_1m"] = round((current / closes[-20] - 1) * 100, 1)
            if len(closes) >= 60:
                entry["chg_3m"] = round((current / closes[-60] - 1) * 100, 1)
            if len(closes) >= 120:
                entry["chg_6m"] = round((current / closes[-120] - 1) * 100, 1)

        # Average volume (20 sessions)
        if len(volumes) >= 20:
            entry["vol_avg20"] = round(sum(volumes[-20:]) / 20, 0)

        result[ticker] = entry

    return result


def _format_technicals_for_prompt(technicals: dict[str, dict]) -> str:
    """Formatuje wskaźniki techniczne jako sekcję promptu."""
    if not technicals:
        return ""

    lines = []
    for ticker, t in sorted(technicals.items()):
        rsi = f"{t['rsi14']:.0f}" if t.get("rsi14") is not None else "-"
        sma = f"{t['sma20_pct']:+.1f}%" if t.get("sma20_pct") is not None else "-"
        c1m = f"{t['chg_1m']:+.1f}%" if t.get("chg_1m") is not None else "-"
        c3m = f"{t['chg_3m']:+.1f}%" if t.get("chg_3m") is not None else "-"
        c6m = f"{t['chg_6m']:+.1f}%" if t.get("chg_6m") is not None else "-"
        vol = f"{t['vol_avg20']:.0f}" if t.get("vol_avg20") is not None else "-"
        h52 = f"{t['high_52w']:.1f}" if t.get("high_52w") is not None else "-"
        l52 = f"{t['low_52w']:.1f}" if t.get("low_52w") is not None else "-"
        lines.append(f"  {ticker:10} {rsi:>5} {sma:>8} {c1m:>8} {c3m:>8} {c6m:>8} {vol:>8} {h52:>8}/{l52}")

    if not lines:
        return ""

    header = f"  {'Ticker':10} {'RSI14':>5} {'SMA20':>8} {'Zm.1M':>8} {'Zm.3M':>8} {'Zm.6M':>8} {'Vol.avg':>8} {'52w H/L':>17}"
    return (
        "═══════════════════════════════════════\n"
        "WSKAŹNIKI TECHNICZNE\n"
        "═══════════════════════════════════════\n"
        "Obliczone z danych OHLCV. RSI>70=wykupiony, RSI<30=wyprzedany.\n"
        "SMA20=odległość od średniej 20-sesyjnej. Zmiany cenowe 1M/3M/6M.\n\n"
        f"{header}\n" + "\n".join(lines)
    )


# ── Sekcja finansowa ────────────────────────────────────────────────────────

def _build_financial_section(
    enrichment: dict[str, dict],
    relevant_companies: set[str],
) -> str:
    """Buduje sekcję WSKAŹNIKI FINANSOWE + REKOMENDACJE + DYWIDENDY z bankier_raw.

    Filtruje do relevant_companies. Graceful: brak danych = pominięcie.
    """
    fin_lines = []
    rec_lines = []
    div_lines = []

    for company_name in sorted(relevant_companies):
        info = enrichment.get(company_name)
        if not info:
            continue
        ticker = info.get("ticker", "?")
        raw = info.get("_bankier_raw") or {}
        if not raw:
            continue

        # Wskaźniki finansowe
        fins = raw.get("wskazniki_finansowe") or {}
        if fins:
            # Sanitize: "0,00"/"-- --"/None → "brak danych" (Bankier placeholder).
            # WAŻNE (regresja 19.04): bez tego Gemini interpretuje "0,00" jako
            # "P/E zero, brak zysku" → masowe SPRZEDAJ na fałszywych danych.
            pe = _format_financial_value(fins.get("pe_ratio") or fins.get("c_z"))
            pbv = _format_financial_value(fins.get("pb_ratio") or fins.get("c_wk"))
            ev = _format_financial_value(fins.get("ev_ebitda"))
            rev = _format_financial_value(fins.get("przychody"))
            profit = _format_financial_value(fins.get("zysk_netto"))
            cap = _format_financial_value(fins.get("kapitalizacja"))
            fin_lines.append(f"  {ticker:10} {pe:>12} {pbv:>12} {ev:>12} {rev:>15} {profit:>15} {cap:>15}")

        # Rekomendacje analityków
        recs = raw.get("rekomendacje_analitykow") or []
        for r in recs[:3]:
            rek = r.get("rekomendacja", "?")
            cena = _format_financial_value(r.get("cena_docelowa"))
            inst = r.get("instytucja") or r.get("dom_maklerski") or "?"
            data = r.get("data", "-")
            rec_lines.append(f"  {ticker:10} {data:>10} {rek:>12} {cena:>12} {inst}")

        # Dywidendy
        divs = raw.get("historia_dywidend") or []
        for d in divs[:3]:
            rok = d.get("rok", "-")
            kwota = _format_financial_value(d.get("kwota_na_akcje") or d.get("kwota"))
            stopa = _format_financial_value(d.get("stopa_dywidendy") or d.get("stopa"))
            data_w = d.get("data_wyplaty") or d.get("data") or "-"
            div_lines.append(f"  {ticker:10} {rok:>6} {kwota:>12} {stopa:>10} {data_w}")

    if not fin_lines and not rec_lines and not div_lines:
        return ""

    sections = []
    sections.append(
        "═══════════════════════════════════════\n"
        "WSKAŹNIKI FINANSOWE SPÓŁEK\n"
        "═══════════════════════════════════════\n"
        "Dane z Bankier.pl. Używaj do walidacji wyceny.\n"
        "⚠️ KRYTYCZNE: 'brak danych' = scraper nie znalazł wartości na stronie.\n"
        "   NIE oznacza zera/straty. NIE używaj 'brak danych' jako uzasadnienia\n"
        "   SPRZEDAJ. Bazuj na rzeczywistych liczbach + rekomendacjach + dywidendach."
    )

    if fin_lines:
        header = f"  {'Ticker':10} {'P/E':>8} {'P/BV':>8} {'EV/EBITDA':>10} {'Przychody':>12} {'Zysk netto':>12} {'Kap.':>12}"
        sections.append(f"\n{header}\n" + "\n".join(fin_lines))

    if rec_lines:
        header = f"\n  REKOMENDACJE ANALITYKÓW\n  {'Ticker':10} {'Data':>10} {'Rekomendacja':>12} {'Cena doc.':>10} Instytucja"
        sections.append(f"{header}\n" + "\n".join(rec_lines))

    if div_lines:
        header = f"\n  DYWIDENDY (historia)\n  {'Ticker':10} {'Rok':>6} {'Kwota/akcję':>12} {'Stopa':>8} Data wypłaty"
        sections.append(f"{header}\n" + "\n".join(div_lines))

    return "\n".join(sections)


# ── Live scraping dla dwuetapowej analizy ────────────────────────────────────

def _live_scrape_candidates(tickers: list[str]) -> dict[str, dict]:
    """Live scraping Bankier dla kandydatów z etapu 1 (screening).

    Returns: {ticker: {wskazniki_finansowe, rekomendacje_analitykow, historia_dywidend}}
    Graceful: błąd scrapingu → pomijamy ticker (broker działa bez tych danych).
    """
    if not tickers:
        return {}

    from agents.company_profiler import scrape_company_data

    result: dict[str, dict] = {}
    for ticker in tickers:
        try:
            data = scrape_company_data(ticker)
            result[ticker] = {
                "wskazniki_finansowe":     data.get("wskazniki_finansowe", {}),
                "rekomendacje_analitykow": data.get("rekomendacje_analitykow", []),
                "historia_dywidend":       data.get("historia_dywidend", []),
            }
            logger.info(f"Live scrape {ticker}: OK")
        except Exception as e:
            logger.warning(f"Live scrape {ticker} failed: {e}")

    logger.info(f"Live scraping: {len(result)}/{len(tickers)} spółek")
    return result


def _format_live_financials_for_prompt(financials: dict[str, dict]) -> str:
    """Formatuje live dane z Bankier jako sekcję promptu etapu 2 (Due Diligence)."""
    if not financials:
        return ""

    fin_lines = []
    rec_lines = []
    div_lines = []

    for ticker in sorted(financials):
        data = financials[ticker]

        fins = data.get("wskazniki_finansowe") or {}
        if fins:
            # Sanitize: "0,00"/"-- --"/None → "brak danych" (Bankier placeholder).
            pe = _format_financial_value(fins.get("pe_ratio") or fins.get("c_z"))
            pbv = _format_financial_value(fins.get("pb_ratio") or fins.get("c_wk"))
            ev = _format_financial_value(fins.get("ev_ebitda"))
            rev = _format_financial_value(fins.get("przychody"))
            profit = _format_financial_value(fins.get("zysk_netto"))
            cap = _format_financial_value(fins.get("kapitalizacja"))
            fin_lines.append(
                f"  {ticker:10} {pe:>12} {pbv:>12} {ev:>12} {rev:>15} {profit:>15} {cap:>15}")

        recs = data.get("rekomendacje_analitykow") or []
        for r in recs[:3]:
            rek = r.get("rekomendacja", "?")
            cena = _format_financial_value(r.get("cena_docelowa"))
            inst = r.get("dom_maklerski") or r.get("instytucja") or "?"
            data_r = r.get("data", "-")
            rec_lines.append(f"  {ticker:10} {data_r:>10} {rek:>12} {cena:>12} {inst}")

        divs = data.get("historia_dywidend") or []
        for d in divs[:3]:
            rok = d.get("rok", "-")
            kwota = _format_financial_value(d.get("kwota_na_akcje") or d.get("kwota"))
            stopa = _format_financial_value(d.get("stopa_dywidendy") or d.get("stopa"))
            data_w = d.get("data_wyplaty") or d.get("data") or "-"
            div_lines.append(f"  {ticker:10} {rok:>6} {kwota:>12} {stopa:>10} {data_w}")

    if not fin_lines and not rec_lines and not div_lines:
        return ""

    sections = [
        "═══════════════════════════════════════\n"
        "LIVE WSKAŹNIKI FINANSOWE (Bankier.pl — dane z dzisiaj)\n"
        "═══════════════════════════════════════\n"
        "Dane pobrane na żywo. Użyj do weryfikacji wyceny kandydatów.\n"
        "⚠️ KRYTYCZNE: 'brak danych' = scraper nie znalazł wartości na stronie.\n"
        "   NIE oznacza zera/straty. NIE odrzucaj spółki z 'brak danych' P/E\n"
        "   jako 'nieuzasadnione P/E'. Bazuj na rzeczywistych liczbach + dywidendach."
    ]

    if fin_lines:
        header = f"  {'Ticker':10} {'P/E':>8} {'P/BV':>8} {'EV/EBITDA':>10} {'Przychody':>12} {'Zysk netto':>12} {'Kap.':>12}"
        sections.append(f"\n{header}\n" + "\n".join(fin_lines))

    if rec_lines:
        header = f"\n  REKOMENDACJE ANALITYKÓW (live)\n  {'Ticker':10} {'Data':>10} {'Rekomendacja':>12} {'Cena doc.':>10} Dom maklerski"
        sections.append(f"{header}\n" + "\n".join(rec_lines))

    if div_lines:
        header = f"\n  DYWIDENDY (historia)\n  {'Ticker':10} {'Rok':>6} {'Kwota/akcję':>12} {'Stopa':>8} Data wypłaty"
        sections.append(f"{header}\n" + "\n".join(div_lines))

    return "\n".join(sections)


def _format_macro_for_prompt(macro: dict | None) -> str:
    """Formatuje pełne dane makro dla promptu brokera (Luka 2).

    Zawiera WSZYSTKIE kategorie z macro_data: indeksy GPW (wszystkie, nie top 10),
    indeksy zagraniczne (S&P, DAX), waluty (USD/PLN, EUR/PLN), surowce
    (ropa, złoto), makro_pl (stopy NBP + CPI).

    Poprzednia implementacja: tylko top 10 indeksów GPW → broker nie wiedział
    np. o kursach walut, ropie, zagranicznych giełdach, stopach NBP.
    """
    if not macro:
        return ""

    sections = [
        "═══════════════════════════════════════",
        "KONTEKST MAKRO (pełny)",
        "═══════════════════════════════════════",
    ]

    gpw = macro.get("indeksy_gpw") or {}
    if gpw:
        sections += [
            "── Indeksy GPW (wszystkie) ──",
            json.dumps(gpw, ensure_ascii=False, indent=2),
            "",
        ]

    foreign = macro.get("indeksy") or {}
    if foreign:
        sections += [
            "── Indeksy zagraniczne ──",
            json.dumps(foreign, ensure_ascii=False, indent=2),
            "",
        ]

    currencies = macro.get("waluty") or {}
    if currencies:
        sections += [
            "── Waluty ──",
            json.dumps(currencies, ensure_ascii=False, indent=2),
            "",
        ]

    commodities = macro.get("surowce") or {}
    if commodities:
        sections += [
            "── Surowce ──",
            json.dumps(commodities, ensure_ascii=False, indent=2),
            "",
        ]

    makro_pl = macro.get("makro_pl") or {}
    if makro_pl:
        sections += [
            "── Makro PL (NBP + CPI) ──",
            json.dumps(makro_pl, ensure_ascii=False, indent=2),
            "",
        ]

    return "\n".join(sections)


def _build_extra_context(
    date_from: date,
    date_to: date,
    prev_report: dict | None,
    enrichment: dict[str, dict] | None = None,
    short: bool = False,
    technicals: dict[str, dict] | None = None,
    relevant_companies: set[str] | None = None,
) -> str:
    """Buduje dodatkowy kontekst: polityka + wiki + lessons + makro + financials + technicals + prev + watchlist.

    `enrichment` — {nazwa_pelna: {ticker, sektor, makrosektor, _bankier_raw, ...}}.
    `short` — True → broker_short (osobne tabele, strategia, lekcje).
    `technicals` — {ticker: {rsi14, sma20_pct, chg_1m, ...}} — opcjonalnie.
    `relevant_companies` — set nazw spółek do sekcji finansowej — opcjonalnie.
    """
    sections = []

    # Statyczna polityka brokera (strategy.md + rules.md) — najwyższy priorytet
    policy = _load_broker_policy(short=short)
    if policy["strategy"] or policy["rules"]:
        sections.append(
            "═══════════════════════════════════════\n"
            "POLITYKA BROKERA (strategy.md + rules.md)\n"
            "═══════════════════════════════════════\n"
            "Statyczna polityka inwestycyjna zdefiniowana przez właściciela portfela.\n"
            "Przestrzegaj tych reguł — nie ignoruj ich w decyzjach.\n\n"
            "── STRATEGIA ──\n"
            f"{policy['strategy']}\n\n"
            "── WYUCZONE REGUŁY ──\n"
            f"{policy['rules']}"
        )

    # Wiki portfela — aktywne pozycje z broker_positions_wiki
    positions_json = _load_positions_wiki_for_context(enrichment=enrichment, short=short)
    if positions_json:
        sections.append(
            "═══════════════════════════════════════\n"
            "WIKI PORTFELA — AKTUALNE POZYCJE\n"
            "═══════════════════════════════════════\n"
            "Dla każdej aktywnej pozycji masz pamięć historyczną\n"
            "(entry thesis, ewolucja tezy, catalysts, ostatnie check-iny):\n\n"
            f"{positions_json}"
        )

    # Lessons — ostatnie 20 z broker_lessons
    lessons_json = _load_recent_lessons_for_context(short=short)
    if lessons_json:
        sections.append(
            "═══════════════════════════════════════\n"
            "LEKCJE Z POPRZEDNICH DECYZJI (ostatnie 20)\n"
            "═══════════════════════════════════════\n"
            "Historyczne post-mortem z poprzednich transakcji i obserwacji.\n"
            "Wyciągaj wnioski — nie powtarzaj błędów:\n\n"
            f"{lessons_json}"
        )

    # Makro — pełny kontekst (Luka 2): indeksy GPW + zagraniczne + waluty
    # + surowce + NBP + CPI. Wcześniej tylko top 10 indeksów GPW.
    try:
        macro = get_bq_client().load_macro(date_to)
        macro_section = _format_macro_for_prompt(macro)
        if macro_section:
            sections.append(macro_section)
    except Exception as e:
        logger.warning(f"Brak danych makro: {e}")

    # Wskaźniki techniczne (RSI, SMA, zmiany cenowe)
    # Uwaga: wskaźniki FINANSOWE (P/E, rekomendacje) NIE są tu — dostarczane
    # live w etapie 2 (Due Diligence) via _live_scrape_candidates().
    if technicals:
        tech_section = _format_technicals_for_prompt(technicals)
        if tech_section:
            sections.append(tech_section)

    # Poprzedni raport — szczegółowy, z wymuszoną ciągłością
    if prev_report:
        comment = prev_report.get("weekly_comment", "")
        buys = prev_report.get("buy_recommendations", [])

        prev_lines = []
        for b in buys:
            prev_lines.append(
                f"  - {b.get('ticker','?')} ({b.get('company','?')}): "
                f"{b.get('amount_pln',0)} PLN, conviction={b.get('conviction','?')}, "
                f"powód: {b.get('reasoning','?')}"
            )

        evals = prev_report.get("portfolio_evaluations", [])
        eval_lines = []
        for ev in evals:
            eval_lines.append(
                f"  - {ev.get('ticker','?')}: {ev.get('recommendation','?')} "
                f"({(ev.get('reasoning') or '')[:80]})"
            )

        watch = prev_report.get("watch_list", [])

        sections.append(
            "═══════════════════════════════════════\n"
            "POPRZEDNIE REKOMENDACJE (tydzień temu)\n"
            "═══════════════════════════════════════\n"
            f"Komentarz: {comment}\n\n"
            f"Rekomendowane zakupy:\n"
            + ("\n".join(prev_lines) if prev_lines else "  (brak)") + "\n\n"
            "Oceny posiadanych pozycji:\n"
            + ("\n".join(eval_lines) if eval_lines else "  (brak)") + "\n\n"
            f"Do obserwacji: {', '.join(watch) if watch else 'brak'}\n\n"
            "ZASADA CIĄGŁOŚCI:\n"
            "Dla KAŻDEJ spółki z poprzednich rekomendacji zakupu MUSISZ się rozliczyć\n"
            "w polu 'rozliczenie_poprzednich'. Jeśli nie pojawiły się negatywne sygnały,\n"
            "spółka POWINNA pozostać w rekomendacjach. Zmiana wymaga KONKRETNEGO powodu."
        )

    # Watchlist
    try:
        wl = get_bq_client().load_watchlist(date_to)
        if wl:
            picks = wl.get("top_picks", [])
            pick_tickers = [p.get("ticker", "?") for p in picks[:5]]
            sections.append(
                "═══════════════════════════════════════\n"
                "WATCHLIST (top picks tygodnia)\n"
                "═══════════════════════════════════════\n"
                f"Spółki: {', '.join(pick_tickers)}"
            )
    except Exception as e:
        logger.warning(f"Brak watchlisty: {e}")

    return "\n\n".join(sections)


# ── Budowanie inputu dla Gemini ───────────────────────────────────────────────

def _format_pozycje(pozycje: list[dict]) -> str:
    if not pozycje:
        return "  (brak pozycji — portfel pusty, inwestor dopiero zaczyna)"
    lines = []
    for p in pozycje:
        ticker     = p.get("ticker", "?")
        spolka     = p.get("spolka", "?")
        liczba     = p.get("liczba_akcji", 0)
        avg_cena   = p.get("srednia_cena_zakupu", 0)
        wartosc    = liczba * avg_cena
        data_zakup = p.get("data_zakupu", "?")
        lines.append(
            f"  {ticker:10} | {spolka[:30]:30} | {liczba} szt. "
            f"@ {avg_cena:.2f} PLN = {wartosc:.0f} PLN | kupiono: {data_zakup}"
        )
    return "\n".join(lines)


def _format_pozycje_with_market_value(
    pozycje: list[dict],
    prices: dict[str, list[dict]],
) -> str:
    """Formatuje pozycje z aktualną ceną rynkową (close z ostatniej sesji).

    Używane w etapie DD żeby agent widział ile realnie warte są pozycje.
    """
    if not pozycje:
        return "  (brak pozycji — portfel pusty)"
    lines = []
    for p in pozycje:
        ticker   = p.get("ticker", "?")
        liczba   = p.get("liczba_akcji", 0)
        avg_cena = p.get("srednia_cena_zakupu", 0)

        # Aktualna cena = close z ostatniej sesji
        sessions = prices.get(ticker) or []
        if sessions:
            last_close = sessions[-1].get("close")
            if last_close is not None:
                market_val = liczba * float(last_close)
                pnl = market_val - (liczba * avg_cena)
                pnl_sign = "+" if pnl >= 0 else ""
                lines.append(
                    f"  {ticker:10} | {liczba} szt. × {avg_cena:.2f} (kupno) "
                    f"→ akt. kurs {float(last_close):.2f} → "
                    f"wartość ~{market_val:.0f} PLN ({pnl_sign}{pnl:.0f})"
                )
                continue

        # Fallback: bez aktualnej ceny
        wartosc = liczba * avg_cena
        lines.append(
            f"  {ticker:10} | {liczba} szt. × {avg_cena:.2f} (kupno) "
            f"→ akt. kurs ? → wartość ~{wartosc:.0f} PLN (brak danych cen.)"
        )
    return "\n".join(lines)


def _estimate_cash_after_sells(
    gotowka: float,
    pozycje: list[dict],
    screening_evals: list[dict],
    prices: dict[str, list[dict]],
) -> float:
    """Szacuje gotówkę po sprzedaży pozycji rekomendowanych do SPRZEDAJ/ZAMKNIJ."""
    sell_tickers = {
        o.get("ticker", "")
        for o in screening_evals
        if o.get("rekomendacja", "").upper() in ("SPRZEDAJ", "ZAMKNIJ")
    }
    estimated = gotowka
    for p in pozycje:
        ticker = p.get("ticker", "")
        if ticker not in sell_tickers:
            continue
        liczba = p.get("liczba_akcji", 0)
        sessions = prices.get(ticker) or []
        if sessions:
            last_close = sessions[-1].get("close")
            if last_close is not None:
                estimated += liczba * float(last_close)
                continue
        # Fallback: cena zakupu
        estimated += liczba * p.get("srednia_cena_zakupu", 0)
    return estimated


def _cap_recommendations_after_sells(
    report: dict,
    gotowka: float,
    pozycje: list[dict],
    screening_evals: list[dict],
    prices: dict[str, list[dict]],
    buffer_pct: float = 0.10,
) -> dict:
    """Bug 2026-05-10 fix: cap z uwzglednieniem przychodu ze SPRZEDAJ/ZAMKNIJ.

    Wczesniej `_cap_recommendations_to_budget(report, gotowka)` capowal do
    biezacej kasy → gdy broker decydowal sprzedac duza pozycje (np 360 PLN),
    Gemini zgodnie z promptem proponowal nowe zakupy za ~300 PLN, ale cap
    kompresowal je do gotowki (np 30 PLN) i wszystko odrzucal jako za drogie.

    Wynik: cash_after_buys=2 PLN + buy_recommendations=[] (sprzecznosc).

    Fix: liczymy est_cash = gotowka + sum(SPRZEDAJ/ZAMKNIJ * last_close) i
    dopiero wtedy wolamy cap. Logika zgodna z promptem (cash_note juz mowil
    Gemini'emu o est_cash).
    """
    est_cash = _estimate_cash_after_sells(gotowka, pozycje, screening_evals, prices)
    return _cap_recommendations_to_budget(report, est_cash, buffer_pct=buffer_pct)


def _calc_wartosc(portfolio: dict) -> float:
    total = portfolio.get("gotowka_pln", 0.0)
    for p in portfolio.get("pozycje", []):
        total += p.get("liczba_akcji", 0) * p.get("srednia_cena_zakupu", 0)
    return total


def _aggregate_analyses_by_company(
    analyses: list[dict],
    enrichment: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """
    Agreguje indywidualne analizy ogłoszeń w podsumowanie tygodniowe per spółka.
    Wynik: {company_name: {podsumowanie, sentymenty, kluczowe_fakty, ...}}

    Gdy `enrichment` podany (Luka 3 fix) — dopisuje `ticker`, `sektor` i
    `makrosektor` per spółka z `company_profiles`. Pozwala Gemini egzekwować
    regułę "max 25% w jednym sektorze" ze `strategy.md`.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for a in analyses:
        company = a.get("_company", "UNKNOWN")
        grouped[company].append(a)

    summaries = {}
    for company, items in grouped.items():
        sentymenty  = [i.get("sentiment", "neutralny") for i in items]
        wagi        = [i.get("waga_informacji", "niska") for i in items]
        wplywy      = [i.get("wplyw_na_kurs", "neutralny") for i in items]
        fakty       = [f for i in items for f in (i.get("kluczowe_fakty") or [])[:2]][:8]
        podsumowania = [i.get("podsumowanie", "") for i in items if i.get("podsumowanie")]

        # Dominujący sentyment tygodnia
        sent_counts = {"pozytywny": 0, "negatywny": 0, "neutralny": 0}
        for s in sentymenty:
            key = s.lower()
            if key in sent_counts:
                sent_counts[key] += 1
        dominant_sent = max(sent_counts, key=lambda k: sent_counts[k])

        # Najwyższa waga ogłoszenia w tygodniu
        waga_rank = {"wysoka": 3, "srednia": 2, "niska": 1}
        max_waga  = max(wagi, key=lambda w: waga_rank.get(w, 0), default="niska")

        entry: dict = {
            "liczba_ogloszen":    len(items),
            "sentiment_tygodnia": dominant_sent,
            "max_waga":           max_waga,
            "wplyw_dominujacy":   max(set(wplywy), key=wplywy.count) if wplywy else "neutralny",
            "kluczowe_fakty":     fakty,
            "podsumowanie":       " | ".join(p[:100] for p in podsumowania[:3]),
        }

        # Sector enrichment (Luka 3): ticker + sektor + makrosektor
        if enrichment:
            info = enrichment.get(company) or {}
            entry["ticker"]      = info.get("ticker", "")
            entry["sektor"]      = info.get("sektor", "")
            entry["makrosektor"] = info.get("makrosektor", "")

        summaries[company] = entry

    return summaries


def _format_prices_for_prompt(prices: dict[str, list[dict]], companies: set[str]) -> str:
    """Formatuje dane cenowe do sekcji w prompcie."""
    if not prices:
        return "Brak danych cenowych za ten tydzień."

    lines = []
    for ticker in sorted(prices.keys()):
        sessions = prices[ticker]
        if not sessions:
            continue
        sessions_sorted = sorted(sessions, key=lambda s: s.get("date", ""))
        first = sessions_sorted[0]
        last  = sessions_sorted[-1]

        change = None
        if first.get("open") and last.get("close"):
            change = (last["close"] - first["open"]) / first["open"] * 100

        change_str = f"{change:+.1f}%" if change is not None else "n/d"
        lines.append(
            f"  {ticker:12} O={first['open']:.2f}  C={last['close']:.2f}  "
            f"tydz.zmiana={change_str}  sesji={len(sessions_sorted)}"
        )

    return "\n".join(lines) if lines else "Brak danych cenowych."


def _build_analyses_for_broker(
    analyses: list[dict],
    max_chars: int = 100_000,
    enrichment: dict[str, dict] | None = None,
) -> str:
    """Agreguje analizy per spółka i buduje JSON podsumowań tygodniowych.

    Gdy `enrichment` podany (Luka 3 fix) — każda spółka w wyniku ma dodatkowe
    pola `ticker`, `sektor`, `makrosektor` dla egzekwowania dywersyfikacji.
    """
    summaries = _aggregate_analyses_by_company(analyses, enrichment=enrichment)

    # Sortuj: wysoka waga → pozytywny/negatywny sentyment na górę
    def sort_key(item):
        _, v = item
        waga_rank = {"wysoka": 3, "srednia": 2, "niska": 1}
        sent_rank = {"pozytywny": 2, "negatywny": 2, "neutralny": 0}
        return (
            waga_rank.get(v.get("max_waga", "niska"), 0),
            sent_rank.get(v.get("sentiment_tygodnia", "neutralny"), 0),
            v.get("liczba_ogloszen", 0),
        )

    sorted_summaries = dict(sorted(summaries.items(), key=sort_key, reverse=True))

    result = json.dumps(sorted_summaries, ensure_ascii=False, indent=2)
    if len(result) <= max_chars:
        logger.info(f"Podsumowania tygodniowe: {len(summaries)} spółek")
        return result

    # Przytnij do max_chars — zachowaj spółki z wysoką wagą
    trimmed = {k: v for k, v in list(sorted_summaries.items())[:200]}
    logger.info(f"Przycięto podsumowania do {len(trimmed)} (z {len(summaries)})")
    return json.dumps(trimmed, ensure_ascii=False, indent=2)


# ── Wiki updates z raportu Gemini ────────────────────────────────────────────

def apply_wiki_updates_to_bq(
    wiki_updates: dict,
    report_date: date,
    dry_run: bool = False,
    short: bool = False,
) -> None:
    """Zapisuje pole `wiki_updates` z raportu Gemini do BQ.

    wiki_updates struktura:
      {
        "positions": [
          {
            "ticker": str,
            "current_thesis_new": str,
            "weekly_check_in": {"date", "sentiment", "notes"},
            "catalysts_positive_add": [str],
            "catalysts_negative_add": [str],
            "target_price_new": float | None,
          }
        ],
        "lessons_added": [
          {"ticker", "category", "what_happened", "what_learned", "rule_derived"}
        ]
      }

    Gdy dry_run=True — loguje zmiany bez zapisu do BQ.
    Gdy wiki_updates puste/None — nic nie robi.
    """
    if not wiki_updates:
        return

    positions_updates = wiki_updates.get("positions") or []
    lessons_added     = wiki_updates.get("lessons_added") or []

    if dry_run:
        logger.info(
            f"DRY-RUN: skip wiki updates "
            f"({len(positions_updates)} positions, {len(lessons_added)} lessons)"
        )
        return

    bq = get_bq_client()

    # 1. Positions updates: upsert istniejącej aktywnej pozycji
    for update in positions_updates:
        ticker = update.get("ticker", "")
        if not ticker:
            continue

        existing = bq.load_position_wiki(ticker, active=True, short=short)
        if not existing:
            logger.warning(
                f"wiki_updates.positions: pozycja {ticker} nie istnieje w BQ — "
                "pomijam update (Gemini odwołał się do nieznanej pozycji)"
            )
            continue

        # Merge: new thesis + existing catalysts + new catalysts
        merged_pos = list(existing.get("catalysts_positive") or [])
        for c in update.get("catalysts_positive_add") or []:
            if c and c not in merged_pos:
                merged_pos.append(c)

        merged_neg = list(existing.get("catalysts_negative") or [])
        for c in update.get("catalysts_negative_add") or []:
            if c and c not in merged_neg:
                merged_neg.append(c)

        # PR#12 #6 fix (2026-04-20): target_price_new=0 / current_thesis_new
        # poniżej 15 zn → ZIGNORUJ (nie nadpisuj cennej tezy bezsensowną wartością).
        # Wcześniej `or` na 0 (falsy) zachowywało stare przez przypadek, ale
        # `or ""` na pustym current_thesis_new mogło zniszczyć tezę (np. ".").
        # 15 zn = realny minimum dla sensownej tezy ("Wyniki +20% r/r" = 15).
        new_thesis = update.get("current_thesis_new") or ""
        thesis_safe = new_thesis if len(new_thesis.strip()) >= 15 else None
        new_target = update.get("target_price_new")
        target_safe = new_target if (new_target and float(new_target) > 0) else None

        merged_entry = {
            "ticker":             ticker,
            "spolka":             existing.get("spolka", ""),
            "entry_date":         existing.get("entry_date"),
            "entry_price":        existing.get("entry_price", 0.0),
            "entry_thesis":       existing.get("entry_thesis", ""),
            "current_thesis":     thesis_safe or existing.get("current_thesis", ""),
            "target_price":       target_safe or existing.get("target_price"),
            "stop_loss":          existing.get("stop_loss"),
            "horizon":            existing.get("horizon", "sredniookresowy"),
            "catalysts_positive": merged_pos,
            "catalysts_negative": merged_neg,
            "weekly_check_ins":   existing.get("weekly_check_ins") or [],
            "active":             True,
        }
        try:
            bq.upsert_position_wiki(merged_entry, short=short)
        except Exception as e:
            logger.error(f"Błąd upsert_position_wiki({ticker}): {e}")
            continue

        # Weekly check-in osobno (append via UPDATE ARRAY_CONCAT)
        check_in = update.get("weekly_check_in") or {}
        if check_in.get("date") or check_in.get("sentiment") or check_in.get("notes"):
            try:
                bq.append_weekly_check_in(ticker=ticker, check_in=check_in, short=short)
            except Exception as e:
                logger.error(f"Błąd append_weekly_check_in({ticker}): {e}")

    # 2. Lessons added: append do broker_lessons
    for lesson in lessons_added:
        payload = {
            "lesson_date":        report_date,
            "ticker":             lesson.get("ticker"),
            "category":           lesson.get("category", "entry"),
            "what_happened":      lesson.get("what_happened", ""),
            "what_learned":       lesson.get("what_learned", ""),
            "rule_derived":       lesson.get("rule_derived"),
            "source_report_date": report_date,
        }
        try:
            bq.append_lesson(payload, short=short)
        except Exception as e:
            logger.error(f"Błąd append_lesson ({lesson.get('ticker')}): {e}")

    logger.info(
        f"wiki_updates applied: {len(positions_updates)} positions updates, "
        f"{len(lessons_added)} lessons added"
    )


# ── Shared data loading ──────────────────────────────────────────────────────

def load_shared_broker_data(
    date_from: date, date_to: date,
) -> tuple[list[dict], dict[str, list[dict]], dict[str, dict], dict[str, dict]]:
    """Ładuje dane współdzielone między brokerem standard i short.

    Returns: (analyses, prices, enrichment, technicals).
    Prices loaded with 180-day lookback for technical indicators.
    """
    analyses = _load_weekly_analyses(date_from, date_to)
    # Lookback 180 dni — wystarczy na RSI(14), SMA(20), zmiany 1/3/6M
    lookback_from = date_from - timedelta(days=180)
    prices_bq = _load_weekly_prices_from_bq(lookback_from, date_to)
    enrichment = _build_company_enrichment()
    prices = _fetch_prices_for_analyzed_companies(
        analyses, enrichment, prices_bq, date_from, date_to,
    )
    # Merge full lookback BQ prices with yfinance (for technicals)
    merged_prices = {**prices_bq, **prices}
    technicals = _calc_technical_indicators(merged_prices)
    return analyses, prices, enrichment, technicals


# ── Główna funkcja ────────────────────────────────────────────────────────────

def generate_broker_report(
    date_from: date,
    date_to:   date,
    dry_run:   bool = False,
    shared_analyses:    list[dict] | None = None,
    shared_prices:      dict[str, list[dict]] | None = None,
    shared_enrichment:  dict[str, dict] | None = None,
    shared_technicals:  dict[str, dict] | None = None,
) -> dict | None:
    """
    Generuje tygodniowy raport brokera z BQ:
    1. Portfel z BQ (broker_portfolio)
    2. Analizy z BQ (analyses) — lub shared
    3. Kursy z BQ (prices) — lub shared
    4. Kontekst: policy + wiki + lessons + makro + prev + watchlist
    5. Gemini → raport JSON
    6. Wiki updates (positions thesis + lessons) → BQ [chyba że dry_run]
    """
    logger.info(f"Generuję raport brokera: {date_from} — {date_to}")

    # 1. Portfel
    portfolio = load_portfolio_from_bq()
    gotowka   = portfolio.get("gotowka_pln", 0.0)
    pozycje   = portfolio.get("pozycje", [])
    wartosc   = _calc_wartosc(portfolio)

    logger.info(
        f"Portfel: {len(pozycje)} pozycji, "
        f"gotówka {gotowka:.0f} PLN, wartość ~{wartosc:.0f} PLN"
    )

    # 2. Analizy tygodnia (shared lub ładowane)
    if shared_analyses is not None:
        analyses = shared_analyses
    else:
        analyses = _load_weekly_analyses(date_from, date_to)
    if not analyses:
        logger.warning("Brak analiz za ten tydzień — nie można wygenerować raportu")
        return None

    total_companies = len(set(a.get("_company", "?") for a in analyses))
    logger.info(f"Załadowano {len(analyses)} ogłoszeń z {total_companies} spółek")

    # 3. Kursy (shared lub ładowane)
    if shared_prices is not None and shared_enrichment is not None:
        prices = shared_prices
        enrichment = shared_enrichment
    else:
        prices_bq = _load_weekly_prices_from_bq(date_from, date_to)
        enrichment = _build_company_enrichment()
        prices = _fetch_prices_for_analyzed_companies(
            analyses, enrichment, prices_bq, date_from, date_to,
        )
    company_names = set(a.get("_company", "") for a in analyses)
    prices_str = _format_prices_for_prompt(prices, company_names)

    # Spółki relevantne: z analiz + portfela
    relevant = company_names | {p.get("spolka", "") for p in pozycje}

    # 4. Kontekst dodatkowy (policy + wiki + lessons + makro + financials + technicals + prev + watchlist)
    prev_report = _load_previous_report(date_from)
    extra_context = _build_extra_context(
        date_from, date_to, prev_report, enrichment=enrichment,
        technicals=shared_technicals,
        relevant_companies=relevant,
    )

    # 5. Prompt — analyses wzbogacone o sektor per spółka (Luka 3)
    # Bug 2026-05-10: pokaz pozycje z wartoscia rynkowa zeby Gemini mial czym
    # budzetowac przychod ze SPRZEDAJ przy nowych zakupach (zamiast cost basis).
    pozycje_str   = _format_pozycje_with_market_value(pozycje, prices)
    analyses_json = _build_analyses_for_broker(analyses, enrichment=enrichment)

    prompt = _BROKER_SYSTEM + "\n\n" + _BROKER_TEMPLATE.format(
        date_from           = str(date_from),
        date_to             = str(date_to),
        gotowka_pln         = f"{gotowka:.0f}",
        wartosc_portfela_pln = f"{wartosc:.0f}",
        pozycje_str         = pozycje_str,
        total_announcements = len(analyses),
        total_companies     = total_companies,
        prices_str          = prices_str,
        analyses_json       = analyses_json,
        extra_context       = extra_context,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # ETAP 1: SCREENING — Gemini wybiera kandydatów na podstawie ESPI/EBI + technicals
    # ══════════════════════════════════════════════════════════════════════════
    try:
        screening = call_gemini_json(
            prompt,
            max_retries=2,
            metadata={
                "agent":           "broker_screening",
                "date_from":       str(date_from),
                "date_to":         str(date_to),
                "analyses_count":  len(analyses),
                "total_companies": total_companies,
                "portfolio_value_pln": f"{wartosc:.0f}",
            },
        )
        if not screening:
            logger.error("Gemini nie zwrócił screeningu brokera")
            return None
        logger.info(
            f"Screening: {len(screening.get('rekomendacje_zakupu', []))} zakupów, "
            f"{len(screening.get('ocena_portfela', []))} ocen"
        )
    except Exception as e:
        logger.error(f"Błąd Gemini broker screening: {e}")
        return None

    # Wiki updates z etapu 1 (niezależne od DD)
    wiki_updates = screening.get("wiki_updates") or {}
    if wiki_updates:
        try:
            apply_wiki_updates_to_bq(wiki_updates, report_date=date_to, dry_run=dry_run)
        except Exception as e:
            logger.error(f"Błąd apply_wiki_updates_to_bq: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # ETAP 1.5: LIVE SCRAPING Bankier dla kandydatów
    # ══════════════════════════════════════════════════════════════════════════
    candidate_tickers: list[str] = []
    for r in screening.get("rekomendacje_zakupu", []):
        t = r.get("ticker", "")
        if t and t not in candidate_tickers:
            candidate_tickers.append(t)
    for r in screening.get("rekomendacje_krotkoterminowe", []):
        t = r.get("ticker", "")
        if t and t not in candidate_tickers:
            candidate_tickers.append(t)
    for o in screening.get("ocena_portfela", []):
        t = o.get("ticker", "")
        if t and t not in candidate_tickers:
            candidate_tickers.append(t)

    if not candidate_tickers:
        logger.info("Brak kandydatów do live scraping — zwracam screening")
        return screening

    logger.info(f"Live scraping {len(candidate_tickers)} kandydatów: {candidate_tickers}")
    live_financials = _live_scrape_candidates(candidate_tickers)

    if not live_financials:
        logger.warning("Live scraping nie zwrócił danych — zwracam screening")
        return screening

    # ══════════════════════════════════════════════════════════════════════════
    # ETAP 2: DUE DILIGENCE — Gemini weryfikuje z live danymi finansowymi
    # ══════════════════════════════════════════════════════════════════════════
    kandydaci_str = "\n".join(
        f"  - {r.get('ticker','?')} ({r.get('spolka','?')}): "
        f"conviction={r.get('conviction','?')}, {(r.get('uzasadnienie') or '')[:80]}"
        for r in screening.get("rekomendacje_zakupu", [])
        + screening.get("rekomendacje_krotkoterminowe", [])
    )
    oceny_str = "\n".join(
        f"  - {o.get('ticker','?')}: {o.get('rekomendacja','?')} — {(o.get('uzasadnienie') or '')[:60]}"
        for o in screening.get("ocena_portfela", [])
    )
    live_fin_str = _format_live_financials_for_prompt(live_financials)

    # Pozycje z aktualną wartością rynkową + szacowana gotówka po sprzedażach
    pozycje_dd_str = _format_pozycje_with_market_value(pozycje, prices)
    est_cash = _estimate_cash_after_sells(
        gotowka, pozycje, screening.get("ocena_portfela", []), prices)
    # FIX 2026-04-22: cash_note jako osobna linia ZA " PLN" — nie wciskać '\n' do gotowka_pln
    # (rozbijało template "Dostępna gotówka: {gotowka_pln} PLN" → " PLN" lądowało na nowej linii).
    cash_note = ""
    if est_cash > gotowka + 1:
        cash_note = (
            f"\n  (Szacowana gotówka po sprzedażach z etapu 1: ~{est_cash:.0f} PLN — "
            f"uwzględnij przy alokacji nowych zakupów)"
        )

    dd_prompt = _BROKER_SYSTEM + "\n\n" + _BROKER_DD_TEMPLATE.format(
        date_from         = str(date_from),
        date_to           = str(date_to),
        gotowka_pln       = f"{gotowka:.0f}",
        cash_note         = cash_note,
        pozycje_str       = pozycje_dd_str,
        sentyment_rynku   = screening.get("sentyment_rynku", "neutralny"),
        komentarz_tygodnia = screening.get("komentarz_tygodnia", ""),
        kandydaci_str     = kandydaci_str or "(brak kandydatów)",
        oceny_str         = oceny_str or "(brak pozycji)",
        live_financials   = live_fin_str,
        horyzont_type     = "krotkoterminowy/sredniookresowy/dlugoterminowy",
        extra_fields      = '"rekomendacje_krotkoterminowe": [],',
        sentyment_rynku_val = screening.get("sentyment_rynku", "neutralny"),
        komentarz_val     = screening.get("komentarz_tygodnia", ""),
    )

    try:
        report = call_gemini_json(
            dd_prompt,
            max_retries=2,
            metadata={
                "agent":      "broker_dd",
                "date_from":  str(date_from),
                "date_to":    str(date_to),
                "candidates": len(candidate_tickers),
                "live_data":  len(live_financials),
            },
        )
        if not report:
            logger.warning("DD Gemini nie zwrócił raportu — fallback do screeningu")
            return screening

        # PR#12 CRITICAL #1: odfiltruj halucynacje Gemini — tylko candidate_tickers
        _filter_dd_to_candidates(report, candidate_tickers)
        # Bug 2026-05-10: cap z uwzglednieniem przychodu ze SPRZEDAJ — wczesniej
        # gotowka cap zerowal rekomendacje gdy broker chcial sprzedac wartosciowa pozycje.
        _cap_recommendations_after_sells(
            report, gotowka, pozycje,
            screening.get("ocena_portfela", []), prices,
        )

        # PR#12 #7+#9 fix (2026-04-20): merge ze screeningu pól które DD template
        # forsuje na pustą listę (gubi cenne dane z Etap 1).
        if not report.get("rozliczenie_poprzednich"):
            report["rozliczenie_poprzednich"] = screening.get("rozliczenie_poprzednich", [])
        if not report.get("do_obserwacji"):
            report["do_obserwacji"] = screening.get("do_obserwacji", [])
        # PR#12 #8: wiki_updates ze screeningu (DD template ich nie generuje)
        if not report.get("wiki_updates") and screening.get("wiki_updates"):
            report["wiki_updates"] = screening["wiki_updates"]

        logger.info(
            f"Due Diligence: {len(report.get('rekomendacje_zakupu', []))} zakupów "
            f"(z {len(screening.get('rekomendacje_zakupu', []))} kandydatów)"
        )
        return report
    except Exception as e:
        logger.error(f"Błąd Gemini broker DD: {e}")
        return screening


# ── Broker Short ─────────────────────────────────────────────────────────────

_BROKER_SHORT_SYSTEM = (
    "Jesteś agresywnym traderem krótkoterminowym na GPW.\n"
    "Szukasz szybkich okazji 1-4 tygodnie z konkretnym katalizatorem.\n"
    "Zarządzasz osobnym portfelem krótkoterminowym (niezależnym od portfela średnioterminowego).\n"
    "Twoje decyzje muszą być oparte WYŁĄCZNIE na dostarczonych danych analitycznych.\n"
    "Odpowiadasz WYŁĄCZNIE poprawnym JSON bez żadnych dodatkowych komentarzy ani znaczników Markdown."
)

_BROKER_SHORT_TEMPLATE = """Okres: {date_from} — {date_to}

PORTFEL KRÓTKOTERMINOWY:
- Dostępna gotówka: {gotowka_pln} PLN
- Szacowana wartość portfela: {wartosc_portfela_pln} PLN
- Posiadane pozycje: {pozycje_str}

KURSY TYGODNIOWE (ostatnie sesje):
{prices_str}

PODSUMOWANIA OGŁOSZEŃ TYGODNIA (ESPI/EBI zagregowane, {total_announcements} ogłoszeń, {total_companies} spółek):
{analyses_json}

⚠️ KRYTYCZNE — INTERPRETACJA DANYCH FINANSOWYCH:
Wartość "brak danych" w polu P/E, zysk netto, EV/EBITDA itp. oznacza że
Bankier scraper NIE znalazł wartości na stronie. NIE oznacza zera, straty
ani złych fundamentów. NIE używaj "brak danych" jako uzasadnienia ZAMKNIJ
pozycji ani odmowy nowego zakupu. Bazuj na: rekomendacjach analityków,
dywidendach, kapitalizacji, sentyment ogłoszeń, sektorze, katalizatorach.

DODATKOWY KONTEKST:
{extra_context}

ZADANIE (Broker Short — horyzont 1-4 tygodnie):
1. OCEŃ każdą posiadaną pozycję krótkoterminową:
   - TRZYMAJ: katalizator nadal aktualny, trzymaj do realizacji
   - ZAMKNIJ: katalizator zrealizowany/wygasł, zamknij pozycję (nawet po 1 tygodniu)
   - OBSERWUJ: niepewne — obserwuj przez kolejny tydzień
2. ZAPROPONUJ nowe zakupy krótkoterminowe (max 5):
   - KAŻDA rekomendacja MUSI mieć konkretny katalizator i horyzont w dniach (1-28)
   - EFEKTYWNY BUDŻET = {gotowka_pln} PLN + suma wartości rynkowych pozycji ZAMKNIJ
     (po kursie zamknięcia z sekcji "Aktualne kursy")
   - Przykład: gotówka 30 PLN + ZAMKNIJ pozycji wartej 250 PLN = budżet 280 PLN
   - Konkretne kwoty w PLN per zakup, łącznie ≤ efektywny_budżet × 0.95 (5% bufor)
   - Minimum conviction: SREDNIA
   - JEŚLI rekomendujesz ZAMKNIJ — MUSISZ rozważyć propozycję BUY za uwolniony kapitał
     (chyba że nie ma świeżego katalizatora wartego krótkiej pozycji)
3. OCEŃ ogólną sytuację rynkową pod kątem okazji krótkoterminowych

Zwróć WYŁĄCZNIE poprawny JSON w formacie:

{{
  "data_raportu": "{date_to}",
  "tydzien_od": "{date_from}",
  "tydzien_do": "{date_to}",
  "gotowka_dostepna_pln": 0,
  "ocena_portfela": [
    {{
      "ticker": "TICKER",
      "spolka": "Nazwa spółki",
      "rekomendacja": "TRZYMAJ/OBSERWUJ/ZAMKNIJ",
      "uzasadnienie": "1-2 zdania — status katalizatora",
      "zmiana_sentymentu": "poprawa/bez_zmian/pogorszenie",
      "alerty": []
    }}
  ],
  "rekomendacje_zakupu": [
    {{
      "ticker": "TICKER",
      "spolka": "Nazwa spółki",
      "kwota_pln": 200,
      "conviction": "WYSOKA/SREDNIA",
      "uzasadnienie": "2-3 zdania — dlaczego teraz, jaki katalizator",
      "ryzyka": "1-2 zdania",
      "katalizator": "Konkretne zdarzenie (wyniki, dywidenda, kontrakt, indeks)",
      "horyzont_dni": 14,
      "liczba_ogloszen_tygodniu": 0
    }}
  ],
  "rozliczenie_poprzednich": [
    {{
      "ticker": "TICKER",
      "poprzednia_rekomendacja": "KUPUJ 200 PLN / TRZYMAJ / OBSERWUJ",
      "decyzja": "utrzymuję/zmieniam/zamykam",
      "powod": "1 zdanie — status katalizatora"
    }}
  ],
  "gotowka_po_zakupach_pln": 0,
  "sentyment_rynku": "pozytywny/neutralny/negatywny/mieszany",
  "komentarz_tygodnia": "2-3 zdania — okazje krótkoterminowe na rynku",
  "do_obserwacji": ["ticker1", "ticker2"],
  "wiki_updates": {{
    "positions": [
      {{
        "ticker": "TICKER",
        "current_thesis_new": "1-2 zdania — zaktualizowany status katalizatora",
        "weekly_check_in": {{
          "date": "{date_to}",
          "sentiment": "poprawa/bez_zmian/pogorszenie",
          "notes": "1 zdanie — co się zmieniło"
        }},
        "catalysts_positive_add": [],
        "catalysts_negative_add": [],
        "target_price_new": 0
      }}
    ],
    "lessons_added": [
      {{
        "ticker": "TICKER lub null",
        "category": "entry/exit/sizing/sector/macro",
        "what_happened": "1-2 zdania",
        "what_learned": "1 zdanie — insight",
        "rule_derived": "uogólniona reguła lub null"
      }}
    ]
  }}
}}

Kluczowe zasady Broker Short:
- KAŻDA rekomendacja zakupu MUSI mieć pole "katalizator" z KONKRETNYM nadchodzącym zdarzeniem
- "horyzont_dni" to szacowana liczba dni do realizacji katalizatora (1-28)
- Conviction minimum SREDNIA — nie rekomenduj z NISKA
- Pozycje bez aktywnego katalizatora po 2 tygodniach → ZAMKNIJ
- "gotowka_po_zakupach_pln" = {gotowka_pln} + przychod ze ZAMKNIJ (po kursie zamkniecia) - suma kwota_pln ze wszystkich zakupów
- "ocena_portfela" zawiera wpisy dla KAŻDEJ posiadanej pozycji (może być pusta lista)
- "rekomendacja" to jedno z: TRZYMAJ / OBSERWUJ / ZAMKNIJ (nie SPRZEDAJ — tu używamy ZAMKNIJ)
- "rozliczenie_poprzednich" — dla KAŻDEJ spółki z poprzednich rekomendacji
- "wiki_updates" — WYPEŁNIAJ TYLKO gdy masz KONKRETNE nowe fakty
- "lessons_added" — TYLKO gdy wyciągasz KONKRETNĄ lekcję"""


# ── Etap 2: Due Diligence template (wspólny dla standard i short) ────────────

_BROKER_DD_TEMPLATE = """ETAP 2: WERYFIKACJA FINANSOWA KANDYDATÓW

Jesteś w drugim etapie analizy. W etapie 1 (screening) wybrałeś kandydatów na podstawie
ogłoszeń ESPI/EBI, wskaźników technicznych i kontekstu rynkowego.

Teraz otrzymujesz LIVE dane finansowe z Bankier.pl dla tych kandydatów.
Twoim zadaniem jest ZWERYFIKOWAĆ, POTWIERDZIĆ lub ODRZUCIĆ każdego kandydata.

═══════════════════════════════════════
PORTFEL — STAN AKTUALNY
═══════════════════════════════════════
Dostępna gotówka: {gotowka_pln} PLN{cash_note}
Posiadane pozycje: {pozycje_str}

═══════════════════════════════════════
WYNIKI ETAPU 1 (SCREENING)
═══════════════════════════════════════
Sentyment rynku: {sentyment_rynku}
Komentarz: {komentarz_tygodnia}

Kandydaci do zakupu:
{kandydaci_str}

Ocena posiadanych pozycji:
{oceny_str}

{live_financials}

═══════════════════════════════════════
ZADANIE: WERYFIKACJA I FINALIZACJA
═══════════════════════════════════════
1. Dla KAŻDEGO kandydata z etapu 1 — zweryfikuj wycenę na podstawie live danych:
   - P/E nieuzasadnione wysoki (>50 bez wzrostu) → ODRZUĆ
   - ⚠️ P/E = "brak danych" (placeholder Bankier scrapera) → IGNORUJ ten sygnał,
     NIE odrzucaj na tej podstawie. Bazuj na innych metrykach (rekomendacje
     analityków, dywidendy, kapitalizacja, sektor, ESPI/EBI z etapu 1).
   - Analitycy rekomendują "Sprzedaj" → ODRZUĆ lub obniż conviction
   - Cena docelowa analityków > 20% powyżej kursu → POTWIERDŹ
   - Regularna dywidenda → bonus dla conviction
2. Ustal FINALNE rekomendacje z konkretnymi kwotami PLN
   - EFEKTYWNY BUDŻET = {gotowka_pln} PLN + przychód ze SPRZEDAJ/ZAMKNIJ pozycji
     (cash_note powyżej już szacuje est_cash — używaj tej liczby)
   - Suma rekomendacji zakupu ≤ efektywny_budżet × 0.9
   - Max 5 rekomendacji zakupu
   - Conviction: TYLKO WYSOKA lub SREDNIA
3. Potwierdź lub zmień oceny posiadanych pozycji

Zwróć WYŁĄCZNIE poprawny JSON:
{{
  "data_raportu": "{date_to}",
  "tydzien_od": "{date_from}",
  "tydzien_do": "{date_to}",
  "gotowka_dostepna_pln": {gotowka_pln},
  "ocena_portfela": [
    {{
      "ticker": "TICKER",
      "spolka": "Nazwa",
      "rekomendacja": "TRZYMAJ/OBSERWUJ/SPRZEDAJ",
      "uzasadnienie": "1-2 zdania z uwzględnieniem live danych finansowych",
      "zmiana_sentymentu": "poprawa/bez_zmian/pogorszenie",
      "alerty": []
    }}
  ],
  "rekomendacje_zakupu": [
    {{
      "ticker": "TICKER",
      "spolka": "Nazwa",
      "kwota_pln": 200,
      "conviction": "WYSOKA/SREDNIA",
      "uzasadnienie": "2-3 zdania — z odniesieniem do wskaźników finansowych",
      "ryzyka": "1-2 zdania",
      "horyzont": "{horyzont_type}",
      "liczba_ogloszen_tygodniu": 0
    }}
  ],
  {extra_fields}
  "rozliczenie_poprzednich": [],
  "gotowka_po_zakupach_pln": 0,
  "sentyment_rynku": "{sentyment_rynku_val}",
  "komentarz_tygodnia": "{komentarz_val}",
  "do_obserwacji": []
}}

ZASADY WERYFIKACJI:
- Możesz ODRZUCIĆ kandydata z etapu 1 jeśli live dane to uzasadniają
- Możesz ZMIENIĆ conviction (np. z WYSOKA na SREDNIA jeśli P/E za wysoki)
- Możesz ZMIENIĆ kwoty na podstawie wyceny
- NIE DODAWAJ nowych spółek spoza listy kandydatów
- "gotowka_po_zakupach_pln" = {gotowka_pln} + przychod ze SPRZEDAJ/ZAMKNIJ (po kursie zamkniecia) - suma kwot zakupów"""


def generate_broker_short_report(
    date_from: date,
    date_to:   date,
    dry_run:   bool = False,
    shared_analyses:    list[dict] | None = None,
    shared_prices:      dict[str, list[dict]] | None = None,
    shared_enrichment:  dict[str, dict] | None = None,
    shared_technicals:  dict[str, dict] | None = None,
) -> dict | None:
    """
    Generuje krótkoterminowy raport brokera (Broker Short).
    Osobny portfel, osobna strategia, osobne tabele BQ.
    """
    logger.info(f"Generuję raport Broker Short: {date_from} — {date_to}")

    # 1. Portfel SHORT
    portfolio = load_portfolio_from_bq(short=True)
    gotowka   = portfolio.get("gotowka_pln", 0.0)
    pozycje   = portfolio.get("pozycje", [])
    wartosc   = _calc_wartosc(portfolio)

    logger.info(
        f"Portfel Short: {len(pozycje)} pozycji, "
        f"gotówka {gotowka:.0f} PLN, wartość ~{wartosc:.0f} PLN"
    )

    # 2. Analizy (shared lub ładowane)
    if shared_analyses is not None:
        analyses = shared_analyses
    else:
        analyses = _load_weekly_analyses(date_from, date_to)
    if not analyses:
        logger.warning("Brak analiz za ten tydzień — nie można wygenerować raportu short")
        return None

    total_companies = len(set(a.get("_company", "?") for a in analyses))

    # 3. Kursy (shared lub ładowane)
    if shared_prices is not None and shared_enrichment is not None:
        prices = shared_prices
        enrichment = shared_enrichment
    else:
        prices_bq = _load_weekly_prices_from_bq(date_from, date_to)
        enrichment = _build_company_enrichment()
        prices = _fetch_prices_for_analyzed_companies(
            analyses, enrichment, prices_bq, date_from, date_to,
        )
    company_names = set(a.get("_company", "") for a in analyses)
    prices_str = _format_prices_for_prompt(prices, company_names)

    relevant = company_names | {p.get("spolka", "") for p in pozycje}

    # 4. Kontekst dodatkowy (SHORT policy + wiki + lessons + makro + financials + technicals + prev + watchlist)
    prev_report = _load_previous_report(date_from, short=True)
    extra_context = _build_extra_context(
        date_from, date_to, prev_report, enrichment=enrichment, short=True,
        technicals=shared_technicals,
        relevant_companies=relevant,
    )

    # 5. Prompt
    # Bug 2026-05-10: pozycje z aktualna wartoscia rynkowa (analogicznie standard).
    pozycje_str   = _format_pozycje_with_market_value(pozycje, prices)
    analyses_json = _build_analyses_for_broker(analyses, enrichment=enrichment)

    prompt = _BROKER_SHORT_SYSTEM + "\n\n" + _BROKER_SHORT_TEMPLATE.format(
        date_from            = str(date_from),
        date_to              = str(date_to),
        gotowka_pln          = f"{gotowka:.0f}",
        wartosc_portfela_pln = f"{wartosc:.0f}",
        pozycje_str          = pozycje_str,
        total_announcements  = len(analyses),
        total_companies      = total_companies,
        prices_str           = prices_str,
        analyses_json        = analyses_json,
        extra_context        = extra_context,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # ETAP 1: SCREENING — Gemini wybiera kandydatów short-term
    # ══════════════════════════════════════════════════════════════════════════
    try:
        screening = call_gemini_json(
            prompt,
            max_retries=2,
            metadata={
                "agent":           "broker_short_screening",
                "date_from":       str(date_from),
                "date_to":         str(date_to),
                "analyses_count":  len(analyses),
                "total_companies": total_companies,
                "portfolio_value_pln": f"{wartosc:.0f}",
            },
        )
        if not screening:
            logger.error("Gemini nie zwrócił screeningu Broker Short")
            return None
        logger.info(
            f"Short Screening: {len(screening.get('rekomendacje_zakupu', []))} kandydatów"
        )
    except Exception as e:
        logger.error(f"Błąd Gemini broker short screening: {e}")
        return None

    # Wiki updates z etapu 1
    wiki_updates = screening.get("wiki_updates") or {}
    if wiki_updates:
        try:
            apply_wiki_updates_to_bq(wiki_updates, report_date=date_to,
                                      dry_run=dry_run, short=True)
        except Exception as e:
            logger.error(f"Błąd apply_wiki_updates_to_bq (short): {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # ETAP 1.5: LIVE SCRAPING Bankier
    # ══════════════════════════════════════════════════════════════════════════
    candidate_tickers: list[str] = []
    for r in screening.get("rekomendacje_zakupu", []):
        t = r.get("ticker", "")
        if t and t not in candidate_tickers:
            candidate_tickers.append(t)
    for o in screening.get("ocena_portfela", []):
        t = o.get("ticker", "")
        if t and t not in candidate_tickers:
            candidate_tickers.append(t)

    if not candidate_tickers:
        logger.info("Short: brak kandydatów do live scraping — zwracam screening")
        return screening

    logger.info(f"Short live scraping {len(candidate_tickers)} kandydatów: {candidate_tickers}")
    live_financials = _live_scrape_candidates(candidate_tickers)

    if not live_financials:
        logger.warning("Short: live scraping nie zwrócił danych — fallback do screeningu")
        return screening

    # ══════════════════════════════════════════════════════════════════════════
    # ETAP 2: DUE DILIGENCE — weryfikacja z live danymi
    # ══════════════════════════════════════════════════════════════════════════
    kandydaci_str = "\n".join(
        f"  - {r.get('ticker','?')} ({r.get('spolka','?')}): "
        f"katalizator={r.get('katalizator','?')}, {(r.get('uzasadnienie') or '')[:80]}"
        for r in screening.get("rekomendacje_zakupu", [])
    )
    oceny_str = "\n".join(
        f"  - {o.get('ticker','?')}: {o.get('rekomendacja','?')} — {(o.get('uzasadnienie') or '')[:60]}"
        for o in screening.get("ocena_portfela", [])
    )
    live_fin_str = _format_live_financials_for_prompt(live_financials)

    # Pozycje z aktualną wartością rynkową + szacowana gotówka po sprzedażach
    pozycje_dd_str = _format_pozycje_with_market_value(pozycje, prices)
    est_cash = _estimate_cash_after_sells(
        gotowka, pozycje, screening.get("ocena_portfela", []), prices)
    # FIX 2026-04-22: patrz komentarz w `analyze_broker_period_bq` — cash_note osobno
    cash_note = ""
    if est_cash > gotowka + 1:
        cash_note = (
            f"\n  (Szacowana gotówka po sprzedażach z etapu 1: ~{est_cash:.0f} PLN — "
            f"uwzględnij przy alokacji nowych zakupów)"
        )

    dd_prompt = _BROKER_SHORT_SYSTEM + "\n\n" + _BROKER_DD_TEMPLATE.format(
        date_from          = str(date_from),
        date_to            = str(date_to),
        gotowka_pln        = f"{gotowka:.0f}",
        cash_note          = cash_note,
        pozycje_str        = pozycje_dd_str,
        sentyment_rynku    = screening.get("sentyment_rynku", "neutralny"),
        komentarz_tygodnia = screening.get("komentarz_tygodnia", ""),
        kandydaci_str      = kandydaci_str or "(brak kandydatów)",
        oceny_str          = oceny_str or "(brak pozycji)",
        live_financials    = live_fin_str,
        horyzont_type      = "krotkoterminowy (1-4 tygodnie)",
        extra_fields       = "",
        sentyment_rynku_val = screening.get("sentyment_rynku", "neutralny"),
        komentarz_val      = screening.get("komentarz_tygodnia", ""),
    )

    try:
        report = call_gemini_json(
            dd_prompt,
            max_retries=2,
            metadata={
                "agent":      "broker_short_dd",
                "date_from":  str(date_from),
                "date_to":    str(date_to),
                "candidates": len(candidate_tickers),
                "live_data":  len(live_financials),
            },
        )
        if not report:
            logger.warning("Short DD nie zwrócił raportu — fallback do screeningu")
            return screening

        # PR#12 CRITICAL #1: odfiltruj halucynacje Gemini — tylko candidate_tickers
        _filter_dd_to_candidates(report, candidate_tickers)
        # Bug 2026-05-10: cap z uwzglednieniem przychodu ze ZAMKNIJ (short uzywa ZAMKNIJ).
        _cap_recommendations_after_sells(
            report, gotowka, pozycje,
            screening.get("ocena_portfela", []), prices,
        )

        # PR#12 #7+#9 fix: merge ze screeningu (DD template forsuje pustą listę)
        if not report.get("rozliczenie_poprzednich"):
            report["rozliczenie_poprzednich"] = screening.get("rozliczenie_poprzednich", [])
        if not report.get("do_obserwacji"):
            report["do_obserwacji"] = screening.get("do_obserwacji", [])
        if not report.get("wiki_updates") and screening.get("wiki_updates"):
            report["wiki_updates"] = screening["wiki_updates"]

        logger.info(
            f"Short Due Diligence: {len(report.get('rekomendacje_zakupu', []))} rekomendacji "
            f"(z {len(screening.get('rekomendacje_zakupu', []))} kandydatów)"
        )
        return report
    except Exception as e:
        logger.error(f"Błąd Gemini broker short DD: {e}")
        return screening
