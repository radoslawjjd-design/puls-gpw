"""
Generator wątku "Cytaty dnia GPW" + filtr Gemini ocena cytatów.

Extracted z agents/xpost_agent.py (Faza 4 krok 6/N).

UWAGA (redesign): okno quotes daily jest na liście KILL w F1 (paused).
W F6 cytaty zostaną wchłonięte do sobotniego Thread Tygodniowy (3 cytaty
tygodnia). Moduł zostaje dla backward compat, deprecation w kolejnym
refactorze.
"""
from __future__ import annotations

import logging
from datetime import date

from agents.xpost.base import _call_gemini

logger = logging.getLogger(__name__)


def _normalize_accepted(accepted: list) -> set[int]:
    """Normalize Gemini accepted list — handles [1,3] or [{"id":1},{"id":3}]."""
    ids: set[int] = set()
    for item in accepted:
        if isinstance(item, int):
            ids.add(item)
        elif isinstance(item, dict):
            for key in ("id", "index", "number"):
                if key in item and isinstance(item[key], int):
                    ids.add(item[key])
                    break
            else:
                vals = [v for v in item.values() if isinstance(v, int)]
                if vals:
                    ids.add(vals[0])
    return ids


def filter_quotes_gemini(quotes_data: list[dict]) -> list[dict]:
    """
    Filtruje cytaty przez Gemini — ocenia wartość informacyjną każdego cytatu.
    Zwraca tylko cytaty z oceną >= 4 (skala 1-5) — surowy filtr jakości.
    Jeden Gemini call dla całej listy.
    """
    if not quotes_data:
        return []

    quotes_block = "\n".join(
        f"{i+1}. [{q.get('spolka', '?')}] {q.get('kluczowy_cytat', '')}"
        for i, q in enumerate(quotes_data)
    )

    prompt = f"""Oceń wartość informacyjną każdego cytatu z ogłoszeń GPW.

Skala 1-5:
5 = konkretna kwota, wynik finansowy, % zmiana, decyzja z liczbami
    Przykład: "Zysk netto +34% r/r do 1.2 mld PLN"
    Przykład: "Dywidenda 4.50 PLN/akcja, dzień ustalenia 15.05.2026"
4 = konkretna decyzja lub fakt (dywidenda, kontrakt, przejęcie) nawet bez kwoty
    Przykład: "Podpisano umowę przejęcia 100% udziałów spółki X"
    Przykład: "Zarząd rekomenduje wypłatę dywidendy z zysku za 2025"
3 = istotna informacja korporacyjna (zmiana zarządu, emisja, restrukturyzacja)
    Przykład: "Rada Nadzorcza powołała nowego Prezesa"
2 = ogólnikowy opis bez konkretu, filler korporacyjny
    Przykład: "Spółka kontynuuje strategię rozwoju w kluczowych segmentach"
    Przykład: "Zarząd monitoruje sytuację rynkową i podejmuje działania"
1 = powtórzenie tytułu ogłoszenia, brak treści, pusty nagłówek
    Przykład: "Zarząd informuje o publikacji raportu okresowego"

KRYTERIUM AKCEPTACJI: accepted = TYLKO cytaty ze score >= 4.
Score 1-3 odrzucaj — nie wnoszą wartości dla czytelnika X.

WAŻNE — TŁUMACZENIE:
Jeśli cytat NIE jest w języku polskim (np. angielski, niemiecki itd.), przetłumacz go na polski.
Dodaj numer przetłumaczonego cytatu do "translations" z polskim tekstem.
Zachowaj oryginalne liczby, kwoty i nazwy własne bez zmian.

CYTATY:
{quotes_block}

Zwróć WYŁĄCZNIE JSON (accepted = cytaty ze score >= 4):
{{"accepted": [1, 3, 5, ...], "translations": {{"2": "polski tekst cytatu nr 2", "7": "polski tekst cytatu nr 7"}}}}

Jeśli nie ma cytatów do tłumaczenia, "translations" może być pusty {{}}.
"""

    result = _call_gemini(prompt, metadata={
        "agent":  "xpost_quotes_filter",
        "quotes_count": len(quotes_data),
    })

    if not result or "accepted" not in result:
        logger.warning("Gemini filtr cytatów: fallback — przepuszczam wszystkie")
        return quotes_data

    # Apply translations
    translations = result.get("translations") or {}
    if translations:
        for idx_str, polish_text in translations.items():
            idx = int(idx_str) - 1  # 1-based → 0-based
            if 0 <= idx < len(quotes_data) and polish_text:
                orig = (quotes_data[idx].get("kluczowy_cytat") or "")[:40]
                quotes_data[idx]["kluczowy_cytat"] = polish_text
                logger.info(f"Cytat {idx+1} przetłumaczony: '{orig}...' → PL")
        logger.info(f"Gemini przetłumaczył {len(translations)} cytatów na polski")

    accepted_ids = _normalize_accepted(result["accepted"])
    if not accepted_ids:
        logger.warning("Gemini filtr cytatów: nie udało się odczytać accepted — fallback")
        return quotes_data
    filtered = [q for i, q in enumerate(quotes_data) if (i + 1) in accepted_ids]
    logger.info(
        f"Gemini filtr cytatów: {len(filtered)}/{len(quotes_data)} zaakceptowanych "
        f"(odrzucone: {len(quotes_data) - len(filtered)})"
    )
    return filtered


def generate_xpost_quotes(
    quotes_data: list[dict],
    data: date,
) -> dict:
    """
    Buduje wątek 'Cytaty dnia GPW' deterministycznie — BEZ Gemini.
    Cytaty to dosłowne zdania z ogłoszeń, nie wymagają interpretacji AI.
    quotes_data: [{"spolka": "TICKER", "kluczowy_cytat": "dosłowny cytat"}, ...]
    Zwraca: {"is_thread": True, "tweets": [str, ...]}
    """
    data_short = data.strftime("%d.%m")
    all_tickers = []
    QUOTES_PER_TWEET = 10

    if not any(q.get("kluczowy_cytat") for q in quotes_data):
        logger.warning("Brak cytatów do wygenerowania wątku.")
        return {"is_thread": False, "tweets": [
            f"\U0001f4ca Cytaty dnia GPW | {data_short}\n"
            f"Brak cytatów z ogłoszeń.\n"
            f"#GPW #ESPI #giełda #FinTwit\n\n"
            f"\u2696\ufe0f Nie stanowi rekomendacji inwestycyjnej. "
            f"\u0179ródło: ESPI/EBI. Inwestujesz na własne ryzyko."
        ]}

    # Podziel cytaty na chunki po max 10
    chunks = [quotes_data[i:i + QUOTES_PER_TWEET]
              for i in range(0, len(quotes_data), QUOTES_PER_TWEET)]
    total = len(chunks)
    tweets = []

    for chunk_idx, chunk in enumerate(chunks):
        lines = []
        if chunk_idx == 0:
            if total > 1:
                lines.append(f"\U0001f4ca Cytaty dnia GPW | {data_short} \U0001f9f5")
            else:
                lines.append(f"\U0001f4ca Cytaty dnia GPW | {data_short}")
            lines.append("")

        for q in chunk:
            ticker = q.get("spolka", "?")
            cytat = q.get("kluczowy_cytat", "")
            if not cytat:
                continue
            all_tickers.append(ticker)
            lines.append(f"#{ticker}")
            lines.append(f"\u201E{cytat}\u201D")
            lines.append("")

        # Ostatni chunk: hashtagi + disclaimer
        if chunk_idx == len(chunks) - 1:
            unique_tickers = list(dict.fromkeys(all_tickers))
            ticker_hashtags = " ".join(f"#{t}" for t in unique_tickers)
            lines.append(f"#GPW #ESPI #giełda #FinTwit {ticker_hashtags}")
            lines.append("")
            lines.append(
                "\u2696\ufe0f Nie stanowi rekomendacji inwestycyjnej. "
                "\u0179ródło: ESPI/EBI. Inwestujesz na własne ryzyko."
            )

        if total > 1:
            lines.append(f"{chunk_idx + 1}/{total}")

        tweets.append("\n".join(lines))

    is_thread = len(tweets) > 1
    logger.info(f"xpost quotes zbudowany: {len(tweets)} tweetów, {len(all_tickers)} cytatów")
    return {"is_thread": is_thread, "tweets": tweets}
