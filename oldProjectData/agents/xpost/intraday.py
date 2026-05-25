"""
Generator intraday: premarket / morning / afternoon / afterhours / daily_thread.

Po F6.4: WSZYSTKIE intraday windowy są threadami (X_STRATEGY 2026).
Adaptive thread length wg `agents.xpost.scoring.target_thread_length`:
  - ≥5 strong news (score ≥50) → 7 postów (hook + 5 spółek + close)
  - 3-4 strong → 5 postów (hook + 3 spółki + close)
  - 1-2 strong → 3 posty (hook + 1-2 spółki + close)
  - 0 strong + ≥1 news → SINGLE post (1 spółka, top-1 ranking) — bez hooka/close
  - 0 wszystkiego → SKIP publikacji

UWAGA (redesign): w F6.6 godziny okien zostaną przesunięte w cron + okno
`premarket` zostanie przemianowane na `presession`, `morning` na `morning_recap`.
Sygnatura funkcji + window names pozostają (re-mapping w xpost.py CLI).
"""
from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime

from agents.xpost.base import _SYSTEM, _call_gemini, _sector_emoji
from agents.xpost.formatters import (
    _extract_tweet,
    _fmt_list,
    _fmt_top,
    _merge_top_announcements,
)
from agents.xpost.scoring import rank_news
from agents.xpost.templates import (
    _SINGLE_TEMPLATE,
    _THREAD_TEMPLATE,
)
from agents.xpost.tier_selector import select_by_tiers_with_stats
from config import XPOST_HOT_TIER_MAX_SLOTS, XPOST_HOT_TIERS

logger = logging.getLogger(__name__)


def generate_xpost(
    window: str,
    data: date,
    liczba_ogloszen: int,
    sentyment: dict,
    top_pozytywne: list[dict],
    top_negatywne: list[dict],
    sektory: list[dict] | None = None,
    ocena_ogolna: str = "",
    trendy: list[str] | None = None,
    ryzyka: list[str] | None = None,
    szanse: list[str] | None = None,
    suggestions_context: str = "",   # ← sugestie z poprzedniej walidacji
) -> dict:
    """
    Generuje thread (lub single post) X dla intraday okna.

    Adaptive: liczba postów zależy od liczby strong news (≥50 score).
    Brak danych → zwraca pustą listę (skip publikacji).
    Brak strong, ale ≥1 news → SINGLE post (1 spółka).

    Zwraca: {"is_thread": bool, "tweets": [str, ...], "tier_stats": dict}
    """
    _WINDOW_LABELS = {
        "premarket":    ("Przed sesją 00-08:45",   "Przed sesją"),
        "morning":      ("Sesja 08:46-13",         "Sesja"),
        "afternoon":    ("Popołudnie 13-15:30",    "Popołudnie"),
        "closing_bell": ("Po sesji 15:30-17:04",   "Po sesji"),
        "afterhours":   ("Wieczór 17:30-23:59",   "Wieczór"),
        "daily_thread": ("Podsumowanie dnia",      ""),
    }
    okno, okno_short = _WINDOW_LABELS.get(window, (window, window))

    today_str = datetime.now(zoneinfo.ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
    system = _SYSTEM.format(today=today_str)

    suggestions_block = (
        f"\n=== UWAGI SUPERVISORA DO POPRZEDNIEJ WERSJI ===\n"
        f"{suggestions_context}\n"
        f"=== UWZGLĘDNIJ TE UWAGI W NOWEJ WERSJI ===\n"
    ) if suggestions_context else ""

    # ── Adaptive: ranguj newsy, wybierz format ─────────────────────────────
    if window == "daily_thread":
        merged = _merge_top_announcements(top_pozytywne[:5], top_negatywne[:5], 10)
    else:
        merged = _merge_top_announcements(top_pozytywne[:5], top_negatywne[:5], 8)

    # ── Hot-tier selekcja (2026-05-14): T1→T2→T3→T4 kaskada ─────────────────
    merged, tier_stats = select_by_tiers_with_stats(
        merged, XPOST_HOT_TIERS, max_slots=XPOST_HOT_TIER_MAX_SLOTS
    )
    logger.info(
        f"xpost {window}: tier selekcja → {len(merged)} newsów "
        f"({[n.get('spolka') for n in merged]})"
    )

    scored = rank_news(merged)

    if not scored:
        logger.info(f"xpost {window}: brak danych — skip publikacji.")
        return {"is_thread": False, "tweets": [], "tier_stats": tier_stats}

    # daily_thread = flagowiec dnia → max 6 spółek (thread 8: hook+6+close).
    # premarket = pierwsza nitka dnia → max 5 (thread 7: hook+5+close).
    # closing_bell / morning / afternoon / afterhours → max 4 (thread 6: hook+4+close).
    if window == "daily_thread":
        max_companies = 6
    elif window == "premarket":
        max_companies = 5
    else:
        max_companies = 4
    n_strong = sum(1 for _, s in scored if s >= 50)

    # ── Strategy B (2026-04-29, zaktualizowana 2026-05-14) ─────────────────
    # Drama signal: waga=wysoka + directional sentiment = strong, nawet jeśli regex 10.
    # Chroni DIGITANET-style cases.
    def _is_strong(news_dict: dict, regex_score: int) -> bool:
        if regex_score >= 50:
            return True
        waga = (news_dict.get("waga") or "").strip().lower()
        sentyment = (news_dict.get("sentyment") or "").strip().lower()
        return waga == "wysoka" and sentyment in {"pozytywny", "negatywny"}

    _strong_spolki: set[str] = set()
    _all_spolki: set[str] = set()
    for _n, _s in scored:
        _spolka = (_n.get("spolka") or "").strip().upper()
        if not _spolka:
            continue
        _all_spolki.add(_spolka)
        if _is_strong(_n, _s):
            _strong_spolki.add(_spolka)
    n_strong_unique = len(_strong_spolki)
    n_unique_companies = len(_all_spolki)

    # Strategy B escape hatch: 0 strong → SKIP (non-eventy, nie publikujemy)
    if n_strong_unique == 0:
        logger.info(
            f"xpost {window}: 0 strong news ({len(scored)} weak news, {n_unique_companies} unique companies) "
            f"→ skip publikacji (Strategy B escape hatch)."
        )
        return {"is_thread": False, "tweets": []}

    # Tier selector wybrał n_companies — to decyduje o formacie (2026-05-14).
    # SINGLE gdy <= 1 spółka w selekcji; THREAD gdy 2+ spółek.
    n_companies = min(n_unique_companies, max_companies)
    target_n = n_companies + 2  # hook + N spółek + close

    # ── SINGLE post mode: 1 spółka w selekcji ────────────────────────────
    if n_companies <= 1:
        top1 = scored[0][0]
        prompt = (
            f"{system}\n\n"
            + _SINGLE_TEMPLATE.format(
                data           = data.strftime("%d.%m.%Y"),
                data_short     = data.strftime("%d.%m"),
                okno           = okno,
                okno_short     = okno_short,
                top_ogłoszenia = _fmt_top([top1], 1),
                suggestions_context = suggestions_block,
            )
        )
        logger.info(
            f"xpost {window}: {n_strong} strong / {len(scored)} news → SINGLE post "
            f"z top-1 (target_n={target_n} ≤ 3, n_companies ≤ 1)."
        )
        result = _call_gemini(prompt, metadata={
            "agent":           "xpost",
            "window":          window,
            "date":            str(data),
            "liczba_ogloszen": liczba_ogloszen,
            "target_thread_length": 1,
            "n_strong_news":   0,
        })

        if result and "tweets" in result:
            result["tweets"] = [_extract_tweet(t) for t in result["tweets"]]
            result["is_thread"] = False
            logger.info(
                f"xpost wygenerowany ({window}): SINGLE, "
                f"znaki={len(result['tweets'][0]) if result['tweets'] else 0}"
            )
            return result
        logger.warning(f"Gemini fallback (single) dla okna {window}")
        return {"is_thread": False, "tweets": [
            f"📊 GPW {okno_short} | {data.strftime('%d.%m')} — ogłoszenia ESPI/EBI.\n"
            f"#GPW\n⚖️ Nie stanowi rekomendacji inwestycyjnej."
        ]}

    # ── THREAD mode: hook + n_companies spółek + close ────────────────────
    top_news = [n for n, _ in scored[:n_companies]]

    sektory_str = "\n".join(
        f"• {s.get('sektor', '?')}: {s.get('liczba_ogloszen', 0)} ogł."
        for s in (sektory or [])[:6]
    ) or "brak danych"

    if sektory:
        sektory_items = [
            f"{_sector_emoji(s.get('sektor', ''))} {s.get('sektor', '?').split('/')[0]} ({s.get('liczba_ogloszen', 0)})"
            for s in sektory
        ]
        sektory_prebuilt = "📊 Sektory: " + ", ".join(sektory_items)
    else:
        sektory_prebuilt = "📊 Sektory: brak danych"

    # F6.6 Strategy A: prekomputuj WYMAGANE KODY GPW dla każdej spółki w top_news.
    # Gemini omija cashtag gdy nie zna kodu — daj mu ściągawkę:
    #   "CAPITAL → $CPZ", "BOGDANKA → $LWB", "ZUE → $ZUE" (nazwa==kod),
    #   "AFHOL → $AFHOL" (brak w whitelist; fallback do nazwy).
    from utils.gpw_tickers import get_gpw_tickers, name_to_ticker, ticker_to_display_name
    gpw_set = get_gpw_tickers()
    required_lines = []
    for news in top_news:
        spolka = (news.get("spolka") or "").strip().upper()
        if not spolka:
            continue
        kod = name_to_ticker(spolka)
        if kod:
            display = ticker_to_display_name(kod)
            cashtag = f"${kod} {display}" if display else f"${kod}"
            required_lines.append(f"  - {spolka} → {cashtag}")
        elif spolka in gpw_set:
            display = ticker_to_display_name(spolka)
            cashtag = f"${spolka} {display}" if display else f"${spolka}"
            required_lines.append(f"  - {spolka} → {cashtag} (nazwa == kod)")
        else:
            # Brak w company_list, brak w whitelist GPW.
            # Dla wieloczłonowych nazw (espiebi: "FARMY FOTOWOLTAIKI POLSKA")
            # użyj pierwszego słowa jako skrótu — validator akceptuje go w soft mode.
            words = spolka.split()
            if len(words) > 1:
                short = words[0]
                required_lines.append(f"  - {spolka} → ${short} (skrót; brak w whitelist)")
            else:
                required_lines.append(f"  - {spolka} → ${spolka} (brak w whitelist)")
    required_block = (
        "\n=== WYMAGANE KODY GPW DLA TYCH SPÓŁEK (UŻYJ DOKŁADNIE TYCH CASHTAGÓW) ===\n"
        + "\n".join(required_lines)
        + "\nKAŻDY post o spółce MUSI mieć cashtag z TEJ LISTY (nie pomijaj $!).\n"
        + "FORMAT CASHTAGU: gdy podana jest nazwa firmy (np. '$APR Auto Partner SA'),\n"
        + "pisz ZAWSZE '$TICKER NazwaFirmy' w każdym poście (hook, body, closing).\n"
    )

    # ── P1 (2026-04-29): Hook directive — explicit top+/top− pick ─────────
    # Gemini ignoruje "kontrast" w cashtag rules i wybiera pierwsze 2 cashtagi z listy
    # zamiast najmocniejszego pozytywu + najmocniejszego negatywu. Naprawa: explicite
    # wstrzykujemy w prompt KTÓRE spółki użyć w hook'u (najmocniejszy positive + negative
    # po score, zamiast "pierwsze w top_news").
    top_positive = None
    top_negative = None
    for _n, _s in scored:
        sentyment = (_n.get("sentyment") or "").strip().lower()
        if sentyment == "pozytywny" and top_positive is None:
            top_positive = (_n, _s)
        elif sentyment == "negatywny" and top_negative is None:
            top_negative = (_n, _s)
        if top_positive and top_negative:
            break

    # ── Tier-priority hook directive (2026-05-14) ─────────────────────────
    # Gdy T1/T2 w selekcji: tier directive ZASTĘPUJE kontrast score-based
    # i jest wstrzykiwany PRZED target_directive — wyższy priorytet w prompcie.
    # Gdy brak T1/T2: score+sentiment kontrast (Reguła 13) jak dotychczas.
    tier_hook_directive = ""
    hook_contrast_directive = ""

    _top_tier_ticker = None
    _top_tier_news = None
    _top_tier_label = ""
    for _tier_num in sorted(tier_stats.keys()):
        _ts = tier_stats.get(_tier_num, {})
        if _ts.get("selected", 0) > 0 and _ts.get("tickers") and _tier_num <= 2:
            _top_tier_ticker = _ts["tickers"][0]
            _top_tier_label = "T1 portfel własny" if _tier_num == 1 else "T2 WIG20/blue chip"
            for _n, _s in scored:
                if (_n.get("spolka") or "").strip().upper() == _top_tier_ticker:
                    _top_tier_news = _n
                    break
            break

    if _top_tier_ticker and _top_tier_news:
        # Tier-first: T1/T2 MUSI być w hook'u (zastępuje score-based selection)
        _tier_tytul = (_top_tier_news.get("tytul") or "")[:120]
        _contrast_news = None
        for _n, _s in scored:
            if (_n.get("spolka") or "").strip().upper() != _top_tier_ticker:
                _contrast_news = _n
                break
        _c_example = "..."
        _c_line = ""
        if _contrast_news:
            _c_sp = (_contrast_news.get("spolka") or "").strip().upper()
            _c_tytul = (_contrast_news.get("tytul") or "")[:80]
            _c_line = f"  Kontrast (drugi slot hooka): ${_c_sp} — {_c_tytul}\n"
            _c_example = f"${_c_sp} [krótki konkret]"
        tier_hook_directive = (
            f"\n=== HOOK — TIER PRIORYTET (REGUŁA NADRZĘDNA, BEZWZGLĘDNA) ===\n"
            f"🚨 ${_top_tier_ticker} ({_top_tier_label}) MUSI być PIERWSZĄ spółką w hook'u.\n"
            f"  Ogłoszenie: {_tier_tytul}\n"
            f"{_c_line}"
            f"\nFORMAT HOOK:\n"
            f"  linia 1: [emoji] ${_top_tier_ticker} [krótki konkret]. [emoji] {_c_example}.\n"
            f"  linia 2: (pusta)\n"
            f"  linia 3: [Pytanie ramujące thread + 🧵]\n"
            f"\nNIE wstawiaj innych cashtagów w hook'u (pozostałe spółki idą do body/closing).\n"
        )
    elif top_positive and top_negative:
        # Brak T1/T2: score+sentiment kontrast (Reguła 13)
        pos_news, _ = top_positive
        neg_news, _ = top_negative
        pos_spolka = (pos_news.get("spolka") or "").strip().upper()
        neg_spolka = (neg_news.get("spolka") or "").strip().upper()
        pos_tytul = (pos_news.get("tytul") or "")[:120]
        neg_tytul = (neg_news.get("tytul") or "")[:120]
        hook_contrast_directive = (
            f"\n=== HOOK KONTRAST (REGUŁA 13 — TOP POZYTYW vs TOP NEGATYW) ===\n"
            f"⚠️ HOOK MUSI używać DOKŁADNIE TYCH 2 SPÓŁEK jako kontrast pozytyw/negatyw.\n"
            f"NIE wybieraj sam z listy — to są najmocniejsze stories po score:\n"
            f"  Top pozytyw: ${pos_spolka} — {pos_tytul}\n"
            f"  Top negatyw: ${neg_spolka} — {neg_tytul}\n"
            f"\nFORMAT HOOK (linia 1: kontrast, linia 2 pusta, linia 3: pytanie + 🧵):\n"
            f"  [emoji+] ${pos_spolka} [krótki konkret z liczbą]. [emoji−] ${neg_spolka} [krótki konkret z liczbą].\n"
            f"  \n"
            f"  [Pytanie ramujące thread + 🧵]\n"
            f"\nNIE wstawiaj innych cashtagów w hook'u (pozostałe spółki idą do body i closing).\n"
        )

    target_directive = (
        f"\n=== DOCELOWA LICZBA POSTÓW W WĄTKU: {target_n} ===\n"
        f"Wygeneruj DOKŁADNIE {target_n} postów: 1 hook + {n_companies} spółek + 1 close.\n"
        f"\n⚠️ BEZWZGLĘDNIE WAŻNE — LICZBA SPÓŁEK W HOOKU:\n"
        f"  W hooku ZAKAZANE jest pisać 'X newsów / X ogłoszeń / X spółek' GDZIE X != {n_companies}.\n"
        f"  Source data zawiera {len(scored)} ogłoszeń ale w threadzie pokazujesz TYLKO {n_companies}.\n"
        f"  Jeśli wspominasz liczbę w hooku — MUSI być DOKŁADNIE {n_companies} (lub bez liczby wcale).\n"
        f"  ✅ POPRAWNIE: '$X +5%, $Y -3%, $Z dywidenda. Najważniejsze {n_companies} ogłoszeń GPW {{data_short}} 🧵'\n"
        f"  ❌ BŁĘDNIE: 'Najważniejsze {len(scored)} ogłoszeń' (Gemini zazwyczaj tu się myli — NIE rób tego!)\n"
        f"{hook_contrast_directive}"
        f"\nOkno czasowe: {okno} ({okno_short}).\n"
    )

    # ── Cashtag v2 opt-in: użyj nowego template z cashtag-heavy format ────
    from config import XPOST_CASHTAG_V2_WINDOWS

    is_cashtag_v2 = window in XPOST_CASHTAG_V2_WINDOWS

    if is_cashtag_v2:
        from agents.xpost import templates as _tpl
        from agents.xpost.cashtag_rules import CASHTAG_INSTRUCTIONS_PROMPT

        body_template = _tpl._THREAD_TEMPLATE_V2.format(
            okno           = okno,
            data           = data.strftime("%d.%m.%Y"),
            data_short     = data.strftime("%d.%m"),
            top_ogłoszenia = _fmt_top(top_news, n_companies),
            sektory        = sektory_str,
            trendy         = _fmt_list(trendy or [], 4),
            ryzyka         = _fmt_list(ryzyka or [], 4),
            szanse         = _fmt_list(szanse or [], 3),
            cashtag_instructions = CASHTAG_INSTRUCTIONS_PROMPT,
            suggestions_context = suggestions_block,
        )
    else:
        body_template = _THREAD_TEMPLATE.format(
            data           = data.strftime("%d.%m.%Y"),
            data_short     = data.strftime("%d.%m"),
            top_ogłoszenia = _fmt_top(top_news, n_companies),
            sektory        = sektory_str,
            sektory_prebuilt = sektory_prebuilt,
            trendy         = _fmt_list(trendy or [], 4),
            ryzyka         = _fmt_list(ryzyka or [], 4),
            szanse         = _fmt_list(szanse or [], 3),
            suggestions_context = suggestions_block,
        )

    prompt = (
        f"{system}\n\n"
        + body_template
        + required_block
        + tier_hook_directive   # T1/T2: nadrzędna instrukcja hooka (pusta gdy brak T1/T2)
        + target_directive
    )

    result = _call_gemini(prompt, metadata={
        "agent":           "xpost",
        "window":          window,
        "date":            str(data),
        "liczba_ogloszen": liczba_ogloszen,
        "target_thread_length": target_n,
        "n_strong_news":   n_strong,
    })

    if result and "tweets" in result:
        result["tweets"] = [_extract_tweet(t) for t in result["tweets"]]

        # ── Hard truncation (2026-04-29): Gemini ignoruje thread max ─────
        # cashtag_rules.THREAD_LENGTH_LIMITS["morning"].max=4, ale dziś morning
        # wygenerował 6 tweetów (T3-T5 = 17-20 views = wyrzucony attention budget).
        # Smart truncate: zachowaj T0 (hook) + T_last (closing) + środek od początku.
        from agents.xpost.cashtag_rules import get_thread_limits_for_window
        thread_max = get_thread_limits_for_window(window).get("max", 7)
        if len(result["tweets"]) > thread_max and len(result["tweets"]) >= 3:
            original_count = len(result["tweets"])
            hook = result["tweets"][0]
            closing = result["tweets"][-1]
            middle = result["tweets"][1:-1]
            keep_middle = thread_max - 2  # hook + middle + closing
            kept_middle = middle[:keep_middle] if keep_middle > 0 else []
            result["tweets"] = [hook] + kept_middle + [closing]
            logger.warning(
                f"xpost {window}: Gemini overshoot {original_count} → truncated "
                f"do {len(result['tweets'])} (max={thread_max}). Wycięto {original_count - len(result['tweets'])} tweetów ze środka."
            )

        result["is_thread"] = len(result["tweets"]) > 1
        result["tier_stats"] = tier_stats
        logger.info(
            f"xpost wygenerowany ({window}): "
            f"is_thread={result.get('is_thread')}, "
            f"tweets={len(result['tweets'])}/{target_n} (target)"
        )
        return result

    logger.warning(f"Gemini fallback (thread) dla okna {window}")
    fallback_tweets = [
        f"GPW {okno_short} | {data.strftime('%d.%m')} — ogłoszenia ESPI/EBI. 🧵",
        (
            "#GPW\n"
            "⚖️ Nie stanowi rekomendacji inwestycyjnej. "
            "Źródło: ESPI/EBI. Inwestujesz na własne ryzyko."
        ),
    ]
    return {"is_thread": True, "tweets": fallback_tweets, "tier_stats": tier_stats}
