"""
Cashtag rules — single source of truth dla format reguł xpostów (2026-04-28+).

Wynika z analizy viralowego tweeta 2026-04-28 (briefing użytkownika):
- $TICKER cashtagi w każdym tweecie GPW = darmowy zasięg (puste feedy cashtagowe PL)
- Kwoty w PLN obok % = credibility
- 🚀 (zysk >100%) / ⚠️ (strata <-10%) na outlierach
- Closing tweet = lista 5-7 cashtagów (nie narracja)
- Multi-cashtag tej samej spółki gdy naturalnie 2 konteksty
- Anti-pattern: spam (>10 cashtagów = algo X karze)

Wariant długości A (Premium-light):
- Hook: ≤280 zn (mobile-skanowalny)
- Body: ≤500 zn hard, sweet 350-450 (lista bullet, NIE wall-of-text)
- Closing: ≤400 zn (lista TOP movers + disclaimer)

Thread length per okno:
- intraday (premarket/morning/afternoon): 3-4 tweety
- closing_bell / daily_thread: 3-5
- broker_decisions: 3
- weekly (saturday/sunday): 5-7

Compliance guard (agents/xpost_compliance.py) DALEJ wymusza max 1 cashtag/post
przy publikacji na X — to jest bezpiecznik. Defense-in-depth: format z 5-7
cashtagami nigdy nie poleci na X przypadkiem (V_AUTO_PUBLISH=true zostałby
zablokowany przez _validate_all_or_raise()).
"""
from __future__ import annotations

import re

# ─────────────────────────────────────────────────────────────────────────────
# REGEXY
# ─────────────────────────────────────────────────────────────────────────────

# $TICKER: dolar + 2-5 wielkich liter (kody GPW). Wykluczamy $100 (kwoty),
# bo wymaga przynajmniej 2 wielkich liter zaraz po $.
_CASHTAG_REGEX = re.compile(r"\$([A-Z][A-Z0-9]{1,4})\b")

# Kwoty PLN: liczba + opcjonalne mln/mld + zł|PLN. Łapie:
#   "+1 234 zł", "16,52 mln zł", "-7,57 mld PLN", "50 zł"
_PLN_AMOUNT_REGEX = re.compile(
    r"-?\d[\d\s,.]*\s*(?:mln|mld)?\s*(?:zł|PLN)\b",
    re.IGNORECASE,
)

# Outlier emoji
_OUTLIER_ROCKET = "🚀"
_OUTLIER_WARNING = "⚠️"


# ─────────────────────────────────────────────────────────────────────────────
# LIMITY DŁUGOŚCI — Wariant A (Premium-light, konserwatywny)
# ─────────────────────────────────────────────────────────────────────────────

TWEET_LENGTH_LIMITS: dict[str, dict[str, int]] = {
    "hook": {
        "min": 80,        # obniżone z 100 — hook może być krótki ($X +5%, $Y -3% 🧵)
        "sweet_max": 260,
        "hard_max": 280,   # free-tier limit jako self-discipline (skanowalny)
    },
    "body": {
        "min": 100,       # obniżone z 200 — krótkie thready (1 decyzja brokera) OK
        "sweet_max": 450,  # bullet list 4-5 punktów
        "hard_max": 600,  # podniesione z 500 — Gemini overshoot 5-15%
    },
    "closing": {
        "min": 100,       # obniżone z 150 — closing też może być krótki
        "sweet_max": 400,
        "hard_max": 450,  # podniesione z 400 — Gemini overshoot
    },
}


THREAD_LENGTH_LIMITS: dict[str, dict[str, int]] = {
    # Tier-aware limits (2026-05-14): hook + N companies + close.
    # premarket max_companies=5 → max 7; morning/afternoon/afterhours max_companies=4 → max 6.
    "premarket":         {"min": 5, "max": 7},
    "morning":           {"min": 5, "max": 6},
    "afternoon":         {"min": 5, "max": 6},
    "afterhours":        {"min": 5, "max": 6},
    "closing_bell":      {"min": 5, "max": 6},
    "daily_thread":      {"min": 3, "max": 5},
    "broker_decisions":  {"min": 3, "max": 3},
    "broker_duel":       {"min": 3, "max": 4},
    "saturday":          {"min": 5, "max": 7},
    "sunday":            {"min": 5, "max": 7},
}


# ─────────────────────────────────────────────────────────────────────────────
# CASHTAG SCORING — soft warnings dla validatora
# ─────────────────────────────────────────────────────────────────────────────

# Ile cashtagów w closing tweecie (sweet spot — algo X)
CLOSING_CASHTAG_MIN = 2   # poniżej = soft -2
CLOSING_CASHTAG_MAX = 10  # powyżej = soft -3 (spam signal)
CLOSING_CASHTAG_SWEET = (5, 7)


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT INSTRUCTIONS — wstrzykiwane w generatory xpostów
# ─────────────────────────────────────────────────────────────────────────────

CASHTAG_INSTRUCTIONS_PROMPT = """\
=== ZASADY CASHTAG (KRYTYCZNE DLA ZASIĘGU) — 2026-04-28+ ===

1. **$TICKER zawsze**, nigdy samo TICKER ani "spółka XYZ".
   - Każda spółka GPW w tweecie = `$KOD` (np. `$MDG`, `$JWW`, `$XTB`).
   - Polski rynek ma niską konkurencję cashtagów → trafiasz do feedu followerów
     spółki za darmo.

2. **HOOK (1/N) MUSI mieć cashtagi** spółek, o których nitka mówi.
   - Hook bez cashtagów = strata największego zasięgu (najwięcej impressions).
   - Przykład poprawny: "$MDG konwertuje 16,52 mln zł długu, $JWW ⚠️ -7,57 mld PLN przepływów"
   - Przykład błędny: "Konwersja długu i ujemne przepływy. Najważniejsze newsy GPW 🧵"

3. **CLOSING (N/N) = LISTA cashtagów + emoji + PLN** (nie narracja!).
   - Format: emoji + $TICKER + krótka treść/zwrot.
   - Sweet spot: 5-7 cashtagów (mniej = strata zasięgu, więcej = spam signal X).
   - Przykład:
     ```
     🏆 TOP movers dnia:
     🚀 $XYZ +200% (+2 029 zł)
     🟢 $ABC +25%
     ⚠️ $DEF -12% (-680 zł)
     ```

4. **Multi-cashtag celowy** — gdy ta sama spółka pada w 2 KONTEKSTACH różnych,
   pisz `$XYZ` 2× zamiast "spółka X".
   - Przykład: "$MDG aneks z BioFund. $MDG zyskuje płynność, BioFund w akcjonariacie."
   - NIE forsuj — tylko gdy naturalnie pasuje.

5. **Kwoty w PLN obok %** wszędzie gdzie są kwoty (capex, dywidendy, transakcje).
   - Dobrze: "$XYZ +5% (+1 234 zł)"
   - Źle: "$XYZ wzrost o 5%"

5a. **r/r i q/q ZAWSZE gdy możliwe** — priorytet formatu dla wyników finansowych:
   - Zawsze podaj zmianę względną gdy dane pozwalają: "+18% r/r", "-7% q/q", "+5 pp r/r" (marże)
   - Kolejność priorytetu: r/r > q/q > vs konsensus > wartość absolutna osobno
   - Gdy FAKTY mają dwie kwoty za różne okresy — POLICZ % i podaj. WAŻNE: obliczenia
     matematyczne na podanych danych = dozwolone (nie łamią reguły 1 ZAKAZ HALUCYNACJI).
     "zysk Q1 2026: 120 mln zł, zysk Q1 2025: 100 mln zł" → "$XYZ zysk netto 120 mln zł, +20% r/r"
   - Format WYMAGANY: "[kwota bieżąca] ([+/-X%] r/r)" — zmiana W NAWIASIE po kwocie
   - ✅ "$PKO zysk netto 1,2 mld zł (+18% r/r)"
   - ✅ "$XYZ przychody 450 mln zł (+20% r/r, +3% q/q)"
   - ✅ "$ABC dywidenda 2,40 zł/akcję (+14% r/r)"
   - ❌ "$PKO zysk netto +18% r/r do 1,2 mld zł" (zła kolejność)
   - ❌ "$PKO zysk netto 1,2 mld zł, +18% r/r" (przecinek zamiast nawiasu)
   - ❌ "$PKO zysk netto wzrósł" (brak liczb) / "$PKO zysk netto 1,2 mld zł" (brak zmiany)

6. **Outliers oznacz emoji**:
   - 🚀 zysk >100% lub mega-pozytywny event (przejęcie, kontrakt > 100 mln zł)
   - ⚠️ strata <-10% lub mega-negatywny event (going concern, default)
   - 🟢 / 🔴 standardowy zysk / strata

7. **ANTI-PATTERNS (NIE rób):**
   - Spamowanie >10 cashtagów w 1 tweecie = algo X kasuje zasięg
   - Cashtagi spółek których NIE ma w treści = manipulacja, banowalne
   - "$XYZ —" 2× w jednym tweecie (bullet body) = duplikat ticker, validator hardfail
   - Wykrzykniki w narracji ("$XYZ LIDER!!!") = kasuje wiarygodność
   - Buzzwords ("polecam", "warto kupić", "okazja") = compliance fail

═══════════════════════════════════════════════════════════════════════
=== REGUŁY v3 (2026-04-29) — z analizy zasięgów morning 13:11 + afternoon 17:16 ===
═══════════════════════════════════════════════════════════════════════

8. **PRECYZJA LICZB — ZAOKRĄGLAJ AGRESYWNIE**:
   - Kwoty >1 mln: zawsze do "X,Y mln" / "X,Y mld" (NIE "461 704 446,69 zł" → "461,7 mln zł")
   - Procenty: max 1 miejsce po przecinku ("+8,4%" nie "+8,42%")
   - WYJĄTEK: dywidenda na akcję (małe kwoty) — "1,42 zł/akcję" zostaw
   - DLACZEGO: precyzja gr/dziesiątek tys. → oczy się odbijają → scroll trigger
   - ❌ "Łączna kwota dywidendy: 20 118 702,00 zł"
   - ✅ "Łącznie 20,1 mln zł na akcjonariuszy"

9. **MAX 3 BULLETY per tweet body** (z 5+):
   - Każdy bullet ≤ 60 znaków (zwięzły, jedna metryka)
   - Wybierz 3 NAJWAŻNIEJSZE — to nie znaczy że trzeba dodać wszystko co masz
   - **OSTATNIA LINIA każdego deep tweet = PYTANIE CLIFFHANGER z 2-3 hipotezami** (NIE sucha summary!)
   - DLACZEGO: 5 bulletów + identyczny format per tweet = banner blindness, T3-T5 robią 17-20 views

   ❌ ZŁA OSTATNIA LINIA (sucha summary, brak cliffhanger):
     "Maksymalna wartość umowy dla $XYZ to 12,99 mln zł brutto."
     "$ABC odnotowuje wzrost wskaźników w I kwartale 2026 roku."

   ✅ DOBRA OSTATNIA LINIA (pytanie z hipotezami — wymusza zatrzymanie i myślenie):
     "Drugi kontrakt $XYZ w 2 miesiące — trend czy jednorazówka?"
     "$ABC dowozi Q1, ale ratuje rok czy odbicie czas?"
     "Restrukturyzacja, emisja akcji, czy dalsze cięcia kosztów?"
     "Rynek zignoruje wynik, czy szykuje się short squeeze?"
     "Okazja przed dzień dywidendy, czy pułapka na bagholderów?"

   FORMAT każdego deep tweet:
     [emoji] $TICKER — [narracja headline]:

     • [bullet 1 z liczbą]
     • [bullet 2 z liczbą]
     • [bullet 3 z liczbą]

     [PYTANIE CLIFFHANGER z 2-3 hipotezami]

10. **NARRACJA > BUREAUCRATIC LANGUAGE**:
    - ❌ "publikuje wstępne wyniki finansowe za I kw. 2026"
    - ❌ "przedstawia projekty uchwał na Walne Zgromadzenie"
    - ❌ "odnotowało skonsolidowaną stratę netto"
    - ✅ "cicho dowozi Q1 2026"
    - ✅ "boli bilans 2025"
    - ✅ "rekord przychodów"
    - ✅ "bilans pęka"
    - Hook + nagłówek bullet listy = NARRACJA, nie komunikat ESPI

11. **NON-EVENTS NIE NALEŻĄ DO THREADU**:
    - "X nie podpisał umowy z Y", "negocjacje zakończone bez efektu", "opóźnienie raportu"
      = non-event → wyrzuć z body threadu
    - WYJĄTEK 1: jeśli to top story dnia (mało innych newsów) — w hook'u zaznacz dlaczego ważne
    - WYJĄTEK 2: w closing leaderboard dopuszczalne jako one-liner (np. "⚠️ $X — non-deal z $Y")
    - DLACZEGO: ARTGAMES "nie podpisali umowy" w morning 13:11 = 20 views = wyrzucony tweet

12. **CLOSING — BINARY QUESTION (BEZWZGLĘDNIE — Gemini ignoruje to nagminnie)**:

    🚫 ZAKAZANE FRAZY (NIE używaj — algo X karze, ludzie nie odpowiadają):
    - ❌ "Które ogłoszenie było dla Ciebie najważniejsze?"
    - ❌ "Które ogłoszenie miało największy wpływ na rynek?"
    - ❌ "Co o tym myślicie?"
    - ❌ "Jakie wnioski?"
    - ❌ "Czego się spodziewacie?"
    - ❌ Każde generic open-ended bez konkretnych nazw spółek/sytuacji

    ✅ WYMAGANE FORMATY (wybierz JEDEN, zawsze MUSI być słowo "czy"):

    A) Binary między 2 spółkami z threadu:
       "Stawiacie na $X czy $Y jako mocniejszy ruch jutro?"
       "$X dowozi Q1, $Y leci. Kto pierwszy odbije — $X czy $Y?"
       "$X kontrakt 327 mln, $Y dywidenda 100% — który news mocniejszy?"

    B) Binary directional dla 1 top spółki:
       "$X odbicie czy dalszy zjazd?"
       "$X long pre-record date czy short na overvalued?"
       "$X buy the dip czy uciekać przed Q2?"

    C) 3 hipotezy (kiedy temat zasługuje na nuance):
       "Restrukturyzacja, emisja akcji, czy dalsze cięcia kosztów?"
       "$X to lider sektora, czarny koń, czy spóźniony fryzjer?"

    SORTOWANIE leaderboard'a w closing: od najmocniejszego POZYTYWU do najmocniejszego NEGATYWU
    (NIE alfabetycznie, NIE chronologicznie). Eyes naturally read top-down → top story na górze.

    DLACZEGO: closing tweet ma niezależną dystrybucję algo (T6 182 views > T1 168 views w morning
    13:11). Binary question generuje 5-10× więcej reply niż generic — reply'e w pierwszych
    minutach to najsilniejszy sygnał algo X że post jest engaging.

13. **HOOK = 2 CASHTAG KONTRAST + PYTANIE RAMUJĄCE** (zamiast 4-6 cashtagów):
    - Hook: JEDEN top pozytyw + JEDEN top negatyw, w PIERWSZEJ linii
    - Druga linia: pytanie ramujące thread / cliffhanger
    - Pozostałe spółki idą do body i closing leaderboard, NIE hook
    - ❌ "$A +5%, $B -3%, $C dywidenda, $D non-event. Najważniejsze 4 ogłoszenia 🧵"
    - ✅ "$A podnosi dywidendę o 100%. $B wpada w 36 mln straty.\\n\\nKto dziś przeskoczył bar, kto wpadł poniżej? 🧵"
    - DLACZEGO: 4 cashtagi w hooku → split attention → mózg nie wie co istotne
    - Wartość liczbowa MUSI być w pierwszej linii (algorytm + reader's eye scan)
"""


# ─────────────────────────────────────────────────────────────────────────────
# HELPERY
# ─────────────────────────────────────────────────────────────────────────────

def count_cashtags(text: str) -> int:
    """Liczy WSZYSTKIE wystąpienia $TICKER (z powtórzeniami).

    Np. "$MDG i $MDG i $JWW" → 3 (multi-cashtag intentional).
    Dla unikalnych użyj unique_cashtags().
    """
    if not text:
        return 0
    return len(_CASHTAG_REGEX.findall(text))


def unique_cashtags(text: str) -> set[str]:
    """Zbiór unikalnych $TICKER w tekście (bez znaku $)."""
    if not text:
        return set()
    return set(_CASHTAG_REGEX.findall(text))


def count_pln_mentions(text: str) -> int:
    """Liczy kwoty w PLN/zł (w tym mln/mld zł)."""
    if not text:
        return 0
    return len(_PLN_AMOUNT_REGEX.findall(text))


def has_outlier_emoji(text: str) -> bool:
    """True jeśli tekst zawiera 🚀 lub ⚠️."""
    if not text:
        return False
    return _OUTLIER_ROCKET in text or _OUTLIER_WARNING in text


def classify_position(idx: int, total: int) -> str:
    """Klasyfikuje pozycję tweeta w nitce.

    - Single post (total=1) → 'closing' (musi mieć disclaimer)
    - idx=0 i total>1 → 'hook'
    - idx=total-1 → 'closing'
    - else → 'body'
    """
    if total == 1:
        return "closing"
    if idx == 0:
        return "hook"
    if idx == total - 1:
        return "closing"
    return "body"


def get_length_limits_for_position(position: str) -> dict[str, int]:
    """Zwraca limity długości dla pozycji ('hook'/'body'/'closing')."""
    return TWEET_LENGTH_LIMITS.get(position, TWEET_LENGTH_LIMITS["body"])


def get_thread_limits_for_window(window: str) -> dict[str, int]:
    """Zwraca limity długości threadu dla okna. Default 3-5 jeśli nieznane."""
    return THREAD_LENGTH_LIMITS.get(window, {"min": 3, "max": 5})


# Prompt suffix do wstrzyknięcia w generatorach które używają OLDER templatów
# (broker_decisions, saturday, sunday) gdzie pełny rewrite byłby ryzykowny.
# Override note: ostatnia instrukcja w prompcie ma najwyższy priorytet dla LLM.
CASHTAG_V2_OVERRIDE_SUFFIX = """

═══════════════════════════════════════════════════════════════════════
=== UWAGA — OVERRIDE STARYCH REGUŁ STRUKTURALNYCH (cashtag-v2 mode) ===
═══════════════════════════════════════════════════════════════════════

WSZYSTKIE wcześniejsze reguły dotyczące "HOOK BEZ cashtaga" oraz
"hook: [opis bez tickera]" są **NIEAKTUALNE** dla tego okna.

OBOWIĄZUJĄ NOWE REGUŁY CASHTAG (poniżej, najwyższy priorytet):

""" + CASHTAG_INSTRUCTIONS_PROMPT + """

⚡ KRYTYCZNE — KAŻDA SPÓŁKA W BULLET = $TICKER ⚡

W bullet listach (`• ...`) KAŻDA spółka MUSI być zapisana jako $KOD_GPW,
NIGDY jako sama nazwa. To dotyczy WSZYSTKICH bulletów w body, NIE tylko
nagłówków. Gdy nie znasz kodu — użyj nazwy z $ (np. $PASSUS).

PRZYKŁADY:
✅ POPRAWNE bullety:
  • $PAS — Wprowadzenie do obrotu na GPW
  • $KOOL2PLAY — ZWZA ws. pokrycia straty
  • $UNICREDIT — WZA
  • $ASB — Dywidenda 0,35 USD/akcję

❌ BŁĘDNE bullety (TRACI ZASIĘG — algo X tego nie wciąga):
  • PASSUS — Wprowadzenie do obrotu
  • KOOL2PLAY — ZWZA
  • UNICREDIT — WZA
  • ASBIS — Dywidenda

KAŻDA spółka wymieniona w treści posta = JEDEN $TICKER. Nawet jeśli
post jest długą listą (10+ spółek), KAŻDA musi mieć $.

DODATKOWO — POSITION-AWARE LIMITY DŁUGOŚCI (Premium-light, 2026-04-28+):
- HOOK (pierwszy post): ≤280 zn (skanowalny mobile).
- Body (środkowe posty): 200-450 zn sweet spot, ≤600 zn hard. Format
  bullet list `• punkt 1\\n• punkt 2\\n• punkt 3` — NIE wall-of-text!
- CLOSING (ostatni post): 150-380 zn, ≤450 zn hard. Lista cashtagów
  + emoji + disclaimer pełny.

CLOSING TWEET MUSI być LISTĄ cashtagów (5-7 sweet spot), NIE narracją:
  🏆 [Tytuł]:
  🟢 $TICKER1 — [krótki opis lub kwota PLN]
  🚀 $TICKER2 — [krótki opis] (gdy zysk >100%)
  ⚠️ $TICKER3 — [krótki opis] (gdy strata <-10%)
  ...
  [opcjonalne pytanie]
  #GPW
  ⚖️ Nie stanowi rekomendacji inwestycyjnej. ...

═══════════════════════════════════════════════════════════════════════
"""
