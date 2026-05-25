"""
Agent podsumowań — agreguje analizy z BigQuery
i tworzy podsumowanie za wybrany okres (dzień/tydzień/miesiąc/kwartał/półrocze/rok).
"""
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

from storage.bq_client import get_bq_client

# Limity truncation dla promptu Gemini
_MAX_SUMMARY_CHARS = 80_000  # max znaków JSON analiz w prompcie
_MAX_SUMMARY_ITEMS = 200     # max elementów po przycięciu

# Prompty importowane z centralnego pliku
from agents.prompts import (
    MACRO_COMMENTARY_SYSTEM,
    MACRO_COMMENTARY_TEMPLATE,
    SUMMARY_SYSTEM,
    SUMMARY_TEMPLATE,
)


def _date_range(period: str, reference_date: date) -> tuple[date, date]:
    if period == "day":
        return reference_date, reference_date
    elif period == "week":
        # Rollup do poniedziałku tego samego tygodnia roboczego (fix #4, 2026-04-11):
        # dla piątku zwraca (pon, pt), a nie (sob, pt) z weekendowymi pustymi dniami.
        monday = reference_date - timedelta(days=reference_date.weekday())
        return monday, reference_date
    elif period == "month":
        return reference_date.replace(day=1), reference_date
    elif period == "quarter":
        q_start_month = ((reference_date.month - 1) // 3) * 3 + 1
        return reference_date.replace(month=q_start_month, day=1), reference_date
    elif period == "halfyear":
        if reference_date.month <= 6:
            return reference_date.replace(month=1, day=1), reference_date
        else:
            return reference_date.replace(month=7, day=1), reference_date
    elif period == "year":
        return reference_date.replace(month=1, day=1), reference_date
    elif period == "all":
        return date(2000, 1, 1), date(2099, 12, 31)
    else:
        raise ValueError(f"Nieznany okres: {period}")


def _period_label(
    period: str,
    date_from: date,
    date_to: date,
    company: str | None = None,
    mode: str = "both",
) -> str:
    base = {
        "day":      f"Dzień {date_from.strftime('%d.%m.%Y')}",
        "week":     f"Tydzień {date_from.strftime('%d.%m')}–{date_to.strftime('%d.%m.%Y')}",
        "month":    f"Miesiąc {date_from.strftime('%m.%Y')}",
        "quarter":  f"Kwartał Q{((date_from.month - 1) // 3) + 1} {date_from.year}",
        "halfyear": f"Półrocze {'H1' if date_from.month <= 6 else 'H2'} {date_from.year}",
        "year":     f"Rok {date_from.year}",
        "all":      "Cała historia",
    }.get(period, f"{date_from} — {date_to}")

    if mode == "portfolio":
        base += " [portfel]"
    if company:
        base += f" — {company}"
    return base


def _filename_prefix(
    period: str,
    date_from: date,
    date_to: date,
    company: str | None = None,
    mode: str = "both",
) -> str:
    base = {
        "day":      f"dzien_{date_from.strftime('%Y-%m-%d')}",
        "week":     f"tydzien_{date_from.strftime('%Y-%m-%d')}_{date_to.strftime('%Y-%m-%d')}",
        "month":    f"miesiac_{date_from.strftime('%Y-%m')}",
        "quarter":  f"kwartal_Q{((date_from.month - 1) // 3) + 1}_{date_from.year}",
        "halfyear": f"polrocze_{'H1' if date_from.month <= 6 else 'H2'}_{date_from.year}",
        "year":     f"rok_{date_from.year}",
        "all":      "all",
    }.get(period, f"{date_from}_{date_to}")

    if mode == "portfolio":
        base += "_portfel"
    if company:
        base += f"_{company.upper()}"
    return base


def _count_sentiments(analyses: list[dict]) -> tuple[int, int, int]:
    pos = sum(1 for a in analyses if a.get("sentiment") == "pozytywny")
    neg = sum(1 for a in analyses if a.get("sentiment") == "negatywny")
    neu = sum(1 for a in analyses if a.get("sentiment") == "neutralny")
    return pos, neg, neu


def _count_sectors(
    analyses: list[dict], sector_map: dict[str, str] | None = None,
) -> list[dict]:
    """Liczy ogłoszenia per sektor. Priorytet: sector_map z BQ profiles, fallback
    na pole 'sektor' w analizie. Zwraca listę posortowaną malejąco.

    Duplikacja logiki z xpost._count_sectors — celowa, unika cyclicznego
    importu xpost → agents.summary_agent (który xpost już ma late-bound).
    """
    from collections import Counter
    sectors: Counter = Counter()
    for a in analyses:
        company = a.get("_company") or a.get("company") or a.get("spolka", "")
        sektor = ""
        if sector_map:
            sektor = sector_map.get(company, "")
        if not sektor:
            sektor = a.get("sektor") or ""
        if sektor:
            sectors[sektor] += 1
    return [
        {"sektor": s, "liczba_ogloszen": n}
        for s, n in sectors.most_common()
    ]


_SUMMARY_FIELDS = [
    "spolka", "data", "typ", "temat", "sentiment", "waga",
    "wplyw_na_kurs", "kluczowe_fakty", "podsumowanie",
    "rekomendacja", "wplyw_na_wyniki", "szacowany_wplyw",
    "ryzyka", "szanse",
]


def _build_analyses_summary(analyses: list[dict], max_chars: int = _MAX_SUMMARY_CHARS) -> str:
    from utils.analyses import build_truncated_analyses_json
    return build_truncated_analyses_json(
        analyses, _SUMMARY_FIELDS, max_chars=max_chars, max_items=_MAX_SUMMARY_ITEMS,
    )


def _normalize_bq_analysis(row: dict) -> dict:
    """
    Mapuje wiersz BQ (analyses table) na format analizy oczekiwany przez summary_agent.
    BQ przechowuje pola pod nazwami kolumn (np. waga_informacji),
    summary_agent oczekuje aliasów (np. waga).
    Zachowuje ORYGINALNE nazwy + ALIASY dla kompatybilności z _build_analyses_summary.
    """
    ad = row.get("analysis_date")
    return {
        # Pola oczekiwane przez _SUMMARY_FIELDS / Gemini prompt
        "spolka":                    row.get("company", ""),
        "data":                      str(ad) if ad else "",
        "typ":                       row.get("typ_ogloszenia"),
        "temat":                     row.get("temat"),
        "sentiment":                 row.get("sentiment"),
        "waga":                      row.get("waga_informacji"),
        "wplyw_na_kurs":             row.get("wplyw_na_kurs"),
        "kluczowe_fakty":            row.get("kluczowe_fakty") or [],
        "podsumowanie":              row.get("podsumowanie"),
        "rekomendacja":              row.get("rekomendacja_dzialania"),
        "wplyw_na_wyniki":           row.get("wplyw_na_wyniki"),
        "szacowany_wplyw":           row.get("szacowany_wplyw_finansowy"),
        "ryzyka":                    row.get("ryzyka") or [],
        "szanse":                    row.get("szanse") or [],
        # Oryginalne nazwy BQ (zachowane dla innych konsumentów)
        "typ_ogloszenia":            row.get("typ_ogloszenia"),
        "waga_informacji":           row.get("waga_informacji"),
        "rekomendacja_dzialania":    row.get("rekomendacja_dzialania"),
        "szacowany_wplyw_finansowy": row.get("szacowany_wplyw_finansowy"),
        # Cytat dosłowny z ogłoszenia (od 2026-04-02)
        "kluczowy_cytat":            row.get("kluczowy_cytat"),
        # Sektor spółki (od 2026-04-06)
        "sektor":                    row.get("sektor"),
        # Pola wewnętrzne
        "_company":                  row.get("company", ""),
        "_filename":                 row.get("filename", ""),
        "_url":                      row.get("url", ""),
    }


def save_summary_to_bq(
    summary: dict,
    period: str,
    date_from: date,
    date_to: date,
    mode: str = "both",
    company_filter: str | None = None,
):
    """Zapisuje podsumowanie do BigQuery."""
    try:
        get_bq_client().upsert_summary(summary, period, date_from, date_to, mode, company_filter)
        logger.info(f"Podsumowanie zapisane w BQ: {period} ({date_from}–{date_to})")
    except Exception as e:
        logger.error(f"Błąd zapisu podsumowania do BQ: {e}")


def generate_summary(
    period: str,
    reference_date: date,
    portfolio_companies: list[str],
    company_filter: str | None = None,
    mode: str = "both",
    portfolio_bankier_names: dict[str, str] | None = None,
) -> dict | None:
    date_from, date_to = _date_range(period, reference_date)
    period_label       = _period_label(period, date_from, date_to, company_filter, mode)

    logger.info(f"Generuję podsumowanie: {period_label}")

    # Zbuduj zestaw tickerów/nazw portfela do filtrowania
    portfolio_upper: set[str] = set()
    for t in portfolio_companies:
        portfolio_upper.add(t.upper().replace(".PL", "").strip())
    if portfolio_bankier_names:
        for name in portfolio_bankier_names.values():
            portfolio_upper.add(name.upper().strip())

    all_analyses:       list[dict] = []
    portfolio_analyses: list[dict] = []

    # ── BQ (jedna query) ─────────────────────────────────────────────────────
    try:
        bq_rows = get_bq_client().load_analyses_for_period(
            date_from       = date_from,
            date_to         = date_to,
            mode            = mode,
            portfolio_tickers = portfolio_upper if portfolio_upper else None,
            company_filter  = company_filter,
        )
        if bq_rows:
            all_analyses       = [_normalize_bq_analysis(r) for r in bq_rows]
            portfolio_analyses = [a for a in all_analyses if a["_company"].upper() in portfolio_upper]
            logger.info(
                f"BQ: {len(all_analyses)} analiz "
                f"({len(portfolio_analyses)} portfelowych)"
            )
    except Exception as e:
        logger.warning(f"BQ load error: {e}")

    if not all_analyses:
        logger.warning(f"Brak analiz dla okresu {period_label}")
        return None

    logger.info(
        f"Załadowano {len(all_analyses)} analiz "
        f"({len(portfolio_analyses)} portfelowych)"
    )

    positive, negative, neutral = _count_sentiments(all_analyses)
    analyses_json = _build_analyses_summary(all_analyses)

    if portfolio_bankier_names:
        portfolio_tickers = ", ".join(
            f"{ticker} ({name})"
            for ticker, name in sorted(portfolio_bankier_names.items())
        )
    elif portfolio_companies:
        portfolio_tickers = ", ".join(sorted(portfolio_companies))
    else:
        portfolio_tickers = "(brak)"

    prompt = SUMMARY_SYSTEM + "\n\n" + SUMMARY_TEMPLATE.format(
        period_label        = period_label,
        date_from           = str(date_from),
        date_to             = str(date_to),
        company_filter_json = f'"{company_filter}"' if company_filter else "null",
        mode                = mode,
        total_announcements = len(all_analyses),
        positive            = positive,
        negative            = negative,
        neutral             = neutral,
        portfolio_tickers   = portfolio_tickers,
        analyses_json       = analyses_json,
    )

    try:
        from agents.vertex_client import call_gemini_json
        summary = call_gemini_json(
            prompt,
            max_retries=2,
            metadata={
                "agent":     "summary",
                "period":    period_label,
                "date_from": str(date_from),
                "date_to":   str(date_to),
                "mode":      mode,
                "analyses_count": len(all_analyses),
                "company_filter":  company_filter or "",
            },
            # Phase 4 re-enabled 2026-04-23: thinking_budget=1536 (50% bufor vs 1024).
            # Summary wymaga syntezy — lepszy bufor dla spójności narracji.
            thinking_budget=1536,
            # PR#11 #5 fix (2026-04-20): cap z baked-in 65535 → 16384.
            # Summary visible output ~3-5K tok + thinking auto ≤4K = bezpieczne.
            # Eliminuje runaway generation gdy Gemini dostanie ambiguous prompt.
            max_output_tokens=16384,
        )
        if summary:
            # ── Post-process Fix #1+#2 (2026-04-11) ──────────────────────────
            # Gemini hallucinates liczba_ogloszen i sektory_aktywne — hard
            # overwrite z Python-calculated values żeby raport miał dokładne
            # dane z BQ zamiast wymyślonych.
            summary["liczba_ogloszen"] = len(all_analyses)

            sector_map: dict[str, str] = {}
            try:
                sector_map = get_bq_client().load_sector_map() or {}
            except Exception as e:
                logger.warning(f"load_sector_map failed, fallback na pole sektor: {e}")
            summary["sektory_aktywne"] = _count_sectors(all_analyses, sector_map)

            logger.info(f"Podsumowanie wygenerowane: {period_label}")
            return summary
        else:
            logger.error(f"Gemini nie zwrócił poprawnego JSON: {period_label}")
            raise RuntimeError(f"Gemini nie wygenerował podsumowania dla: {period_label}")

    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Błąd wywołania Vertex AI: {e}")
        raise RuntimeError(f"Błąd Vertex AI: {e}") from e


def summary_to_markdown(summary: dict) -> str:
    lines          = []
    company_filter = summary.get("filtr_spolki")
    mode           = summary.get("tryb", "both")

    title = f"# Podsumowanie: {summary.get('okres', '?')}"
    lines.append(title)
    lines.append(f"\n**Okres:** {summary.get('data_od')} — {summary.get('data_do')}")
    if mode == "portfolio":
        lines.append("**Tryb:** Tylko spółki portfelowe")
    if company_filter:
        lines.append(f"**Spółka:** {company_filter}")
    lines.append(f"**Liczba ogłoszeń:** {summary.get('liczba_ogloszen', 0)}")

    s = summary.get("sentyment_rynku", {})
    lines.append("\n## Sentyment")
    lines.append(f"- 📈 Pozytywny: **{s.get('pozytywny', 0)}**")
    lines.append(f"- 📉 Negatywny: **{s.get('negatywny', 0)}**")
    lines.append(f"- ● Neutralny: **{s.get('neutralny', 0)}**")
    if s.get("ocena_ogolna"):
        lines.append(f"\n_{s['ocena_ogolna']}_")

    top_poz = summary.get("top_pozytywne", [])
    top_neg = summary.get("top_negatywne", [])
    top = top_poz + top_neg
    if top:
        lines.append("\n## Top ogłoszenia")
        for i, ann in enumerate(top, 1):
            icon = {"pozytywny": "📈", "negatywny": "📉", "neutralny": "●"}.get(ann.get("sentiment", ""), "•")
            lines.append(f"\n### {i}. {icon} {ann.get('spolka', '?')} — {ann.get('tytul', '?')}")
            lines.append(f"**Waga:** {ann.get('waga', '?')} | **Sentiment:** {ann.get('sentiment', '?')}")
            if ann.get("dlaczego_wazne"):
                lines.append(f"\n{ann['dlaczego_wazne']}")

    portfolio = summary.get("spolki_portfelowe", [])
    if portfolio:
        lines.append("\n## Spółki portfelowe")
        for p in portfolio:
            rec_icon = {
                "rozważ_zwiększenie":  "🟢",
                "obserwuj":            "🟡",
                "trzymaj":             "⚪",
                "rozważ_zmniejszenie": "🔴",
            }.get(p.get("rekomendacja", ""), "•")
            lines.append(f"\n### {rec_icon} {p.get('spolka', '?')}")
            lines.append(
                f"**Ogłoszeń:** {p.get('liczba_ogloszen', 0)} | "
                f"**Sentyment:** {p.get('sentyment_okresu', '?')} | "
                f"**Rekomendacja:** {p.get('rekomendacja', '?')}"
            )
            for ev in p.get("kluczowe_wydarzenia", []):
                lines.append(f"- {ev}")
            if p.get("uzasadnienie"):
                lines.append(f"\n{p['uzasadnienie']}")

    trendy = summary.get("trendy_i_wzorce", [])
    if trendy:
        lines.append("\n## Trendy i wzorce")
        for t in trendy:
            lines.append(f"- {t}")

    ryzyka = summary.get("ryzyka_rynkowe", [])
    if ryzyka:
        lines.append("\n## ⚠️ Ryzyka rynkowe")
        for r in ryzyka:
            lines.append(f"- {r}")

    szanse = summary.get("szanse_rynkowe", [])
    if szanse:
        lines.append("\n## 💡 Szanse rynkowe")
        for sz in szanse:
            lines.append(f"- {sz}")

    broker = summary.get("dane_do_brokera", {})
    if broker:
        lines.append("\n## Dane dla brokera")
        score = broker.get("ogolny_sentyment_score", 0)
        lines.append(f"**Score sentymentu:** {score:+.2f}")
        if broker.get("spolki_do_zwiększenia"):
            lines.append(f"**Do rozważenia — zwiększenie:** {', '.join(broker['spolki_do_zwiększenia'])}")
        if broker.get("spolki_do_zmniejszenia"):
            lines.append(f"**Do rozważenia — zmniejszenie:** {', '.join(broker['spolki_do_zmniejszenia'])}")
        if broker.get("spolki_do_obserwacji"):
            lines.append(f"**Do obserwacji:** {', '.join(broker['spolki_do_obserwacji'])}")
        for a in broker.get("alerty", []):
            lines.append(f"- 🚨 {a}")

    if summary.get("podsumowanie_dla_brokera"):
        lines.append("\n## Podsumowanie")
        lines.append(summary["podsumowanie_dla_brokera"])

    lines.append("\n---\n*Wygenerowano automatycznie przez system oswiadczenia_gwp*")
    return "\n".join(lines)


# ── Komentarz makro (gdy brak ogłoszeń portfela) ───────────────────────────────

def generate_macro_commentary(
    macro: dict,
    profiles: dict[str, dict],
    portfolio_bankier_names: dict[str, str],
) -> dict:
    """
    Generuje komentarz Gemini o portfelu bazując na danych makro i profilach spółek.
    Wywoływany gdy żadna spółka portfelowa nie miała ogłoszeń danego dnia.
    Zwraca dict z kluczami: komentarz, nastroj, kluczowe_czynniki, na_co_uwazac.
    """
    portfolio_tickers = ", ".join(
        f"{t} ({n})" for t, n in sorted(portfolio_bankier_names.items())
    )

    # Formatuj makro kompaktowo
    def _fmt_section(d: dict) -> str:
        lines = []
        for k, v in d.items():
            if isinstance(v, dict):
                cena = v.get("cena") or v.get("kurs", "?")
                zm   = v.get("zmiana_proc")
                line = f"  {k}: {cena}"
                if zm is not None:
                    line += f" ({zm:+.2f}%)"
                lines.append(line)
        return "\n".join(lines) or "  brak danych"

    macro_summary = (
        f"Indeksy:\n{_fmt_section(macro.get('indeksy', {}))}\n"
        f"Surowce:\n{_fmt_section(macro.get('surowce', {}))}\n"
        f"Waluty:\n{_fmt_section(macro.get('waluty', {}))}"
    )

    # Formatuj profile kompaktowo
    profiles_lines = []
    for ticker, profile in profiles.items():
        if not profile:
            continue
        name    = profile.get("nazwa", ticker)
        sektor  = profile.get("sektor", "?")
        opis    = profile.get("opis_dzialalnosci", profile.get("opis", ""))[:200]
        profiles_lines.append(f"  {ticker} ({name}) | {sektor}\n    {opis}")
    profiles_summary = "\n".join(profiles_lines) or "  Brak profili"

    prompt = MACRO_COMMENTARY_SYSTEM + "\n\n" + MACRO_COMMENTARY_TEMPLATE.format(
        portfolio_tickers = portfolio_tickers,
        macro_summary     = macro_summary,
        profiles_summary  = profiles_summary,
    )

    try:
        from agents.vertex_client import call_gemini_json
        result = call_gemini_json(
            prompt,
            max_retries=1,
            metadata={"agent": "summary_macro_commentary"},
            max_output_tokens=4096,  # PR#11 #5: macro commentary krótki ~500 tok
        )
        if result:
            return result
        raise ValueError("Gemini zwrócił None")

    except Exception as e:
        logger.warning(f"Błąd generowania komentarza makro: {e}")
        return {
            "komentarz":         "Brak ogłoszeń portfela. Dane makro dostępne powyżej.",
            "nastroj":           "neutralny",
            "kluczowe_czynniki": [],
            "na_co_uwazac":      "",
        }