"""
Shared base dla generatorów xpost: system prompt, sektory, helpery Gemini.

Extracted z agents/xpost_agent.py (Faza 4 redesignu).
"""
from __future__ import annotations

# ── Etykieta daty ──────────────────────────────────────────────────────────────

def _date_label(d_from: str, d_to: str) -> str:
    """Return 'DD.MM' if single day, 'DD.MM–DD.MM' if range."""
    return d_from if d_from == d_to else f"{d_from}–{d_to}"


# ── Emoji per makrosektor (klucz = makrosektor PRZED "/") ──────────────────────

_SECTOR_EMOJI = {
    "artykuły spożywcze":            "🍞",
    "banki":                         "🏦",
    "biotechnologia":                "🧬",
    "budownictwo":                   "🏗️",
    "chemia":                        "⚗️",
    "drewno i papier":               "🪵",
    "dystrybucja leków":             "💊",
    "działalność inwestycyjna":      "💼",
    "energia":                       "⚡",
    "gry":                           "🎮",
    "guma i tworzywa sztuczne":      "🧪",
    "górnictwo":                     "⛏️",
    "handel hurtowy":                "📦",
    "handel internetowy":            "🛒",
    "hutnictwo":                     "🔩",
    "informatyka":                   "💻",
    "leasing i faktoring":           "📋",
    "media":                         "📺",
    "motoryzacja":                   "🚗",
    "nieruchomości":                 "🏠",
    "nowe technologie":              "🚀",
    "ochrona zdrowia - pozostałe":   "🏥",
    "odzież i kosmetyki":            "👗",
    "paliwa i gaz":                  "⛽",
    "pośrednictwo finansowe":        "💰",
    "produkcja leków":               "💊",
    "przemysł elektromaszynowy":     "⚙️",
    "recykling":                     "♻️",
    "rekreacja i wypoczynek":        "🏖️",
    "rynek kapitałowy":              "📈",
    "sieci handlowe":                "🏪",
    "sprzęt i materiały medyczne":   "🩺",
    "szpitale i przychodnie":        "🏥",
    "telekomunikacja":               "📡",
    "transport i logistyka":         "🚛",
    "ubezpieczenia":                 "🛡️",
    "usługi dla przedsiębiorstw":    "🏢",
    "wierzytelności":                "💳",
    "wyposażenie domu":              "🪑",
    "zaopatrzenie":                  "🔧",
}


def _sector_emoji(sektor: str) -> str:
    """Zwraca emoji dla sektora. Klucz = makrosektor (przed '/')."""
    makrosektor = sektor.split("/")[0].strip().lower()
    return _SECTOR_EMOJI.get(makrosektor, "📌")


# ── System prompt (reguły — JEDNO miejsce, bez powtórzeń) ──────────────────────

_SYSTEM = """Redaktor finansowy posty X o ogłoszeniach GPW. DATA: {today}.

ZASADY (bezwzględne — łamanie = odrzucenie posta):

TREŚĆ:
1. ZAKAZ HALUCYNACJI — WYŁĄCZNIE dane z sekcji wejściowej. Brak kwoty w
   danych = bez kwoty. Lepiej bez liczb niż z wymyślonymi.
2. ZERO porad inwestycyjnych (kupuj/sprzedaj/warto/rekomendacja).
3. Tylko fakty. ZAKAZ sentyment/pozytyw/negatyw — neutralna relacja.
4. NIE "Spółka opublikowała X ogłoszeń" — KONKRETNE fakty z danych.

FORMAT 2026 (X hard limits — compliance guard odrzuca):
5. CASHTAGI: $TICKER (NIE #TICKER). KODY GPW 3-6 zn.: $LWB, $CDR, $PKN,
   $INGBSK, $11B. NIGDY nazwy ($BOGDANKA = błąd → $LWB).
6. MAX 1 cashtag per post (X 2026). Multi-spółka → THREAD (1 spółka/post
   w reply chain). Drugą spółkę w tym samym poście — plain text bez $.
7. MAX 2 hashtagi per post (zalecane 1) — sektor/indeks: #GPW, #WIG20,
   #mWIG40, #sWIG80, #dywidendy. NIGDY #TICKER (używaj $TICKER).
8. MAX 280 znaków per post (twardy limit X — NIE Premium-long).
   Idealne: 71-100 zn. lub 240-270 zn.
9. THREAD multi-spółka: 1.post = HOOK liczba+fakt (bez cashtaga); środek
   = 1 spółka/post; ostatni = pytanie + 1 hashtag + stopka prawna.
   Stopka TYLKO w ostatnim poście (nie powtarzaj).
10. UNIKALNOŚĆ: 1 wpis per spółka per post (łącz fakty tej samej spółki).
11. Zero linków URL (X dusi posty non-Premium z linkami od III.2026).

STYL: emoji max 2/tweet. Polski, neutralny ton.

Odpowiedz WYŁĄCZNIE poprawnym JSON."""


# ── Gemini call wrapper ────────────────────────────────────────────────────────

def _call_gemini(prompt: str, metadata: dict | None = None) -> dict | None:
    from agents.vertex_client import call_gemini_json
    # Phase 4 re-enabled 2026-04-23: thinking_budget=1536 (50% bufor vs 1024).
    # xpost generation jest creative — bufor na dobry hook + spójność threada.
    # max_output_tokens=16384: thinking_budget 1536 + thread 7 tweetów ~5K visible
    # = ~6.5K realnie używane, 16K zostaje jako safety net.
    return call_gemini_json(
        prompt, max_retries=2, metadata=metadata,
        thinking_budget=1536, max_output_tokens=16384,
    )
