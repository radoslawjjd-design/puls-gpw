"""
Prompt templates dla generatorów xpost — format 2026.

Zaktualizowane w F6.3 step 2 redesignu (BACKLOG.md → "X redesign").

Reguły 2026 (powtórzone tu, są też w _SYSTEM):
- max 280 znaków per post (twardy limit X)
- max 1 cashtag $TICKER per post (X 2026 hard limit)
- max 2 hashtagi (zalecane 1: #GPW)
- thread-first dla multi-spółka: 1 spółka = 1 post w reply chain
- pierwszy post threadu: HOOK (liczba+fakt) BEZ cashtaga/hashtaga
- ostatni post threadu: pytanie + 1 hashtag + stopka prawna
- stopka prawna TYLKO w ostatnim poście threadu
- kody GPW (np. $LWB nie $BOGDANKA, $ORL nie $PKNORLEN)

Każdy template = czysty string z placeholderami; logika w generatorach.
"""
from __future__ import annotations

# ── Template: pojedynczy post (sporadycznie używany — większość okien=thread) ─

_SINGLE_TEMPLATE = """=== DANE GPW {okno} | {data} ===

--- NAJWAŻNIEJSZE OGŁOSZENIA ---
{top_ogłoszenia}

{suggestions_context}=== ZADANIE ===
Napisz JEDEN post (max 280 znaków, idealne 200-270).

ZASADY (X 2026):
- MAX 1 cashtag $TICKER (najważniejsza spółka tego posta).
- Pozostałe spółki — plain text bez $/#.
- MAX 1 hashtag (zalecane #GPW).
- Brak linków.
- OBOWIĄZKOWY disclaimer prawny w ostatniej linii (compliance).

Format:
📊 GPW {okno_short} | {data_short}
[emoji sektora] $TICKER — fakt z danych + zwięzły kontekst.
#GPW
⚖️ Nie stanowi rekomendacji inwestycyjnej.

Przykład poprawnego JSON:
{{"is_thread": false, "tweets": ["📊 GPW {okno_short} | {data_short}\\n🏦 $PKO — Zysk netto 1.2 mld PLN w Q1 2026, +18% r/r.\\n#GPW\\n⚖️ Nie stanowi rekomendacji inwestycyjnej."]}}"""


# ── Template: wątek (daily_thread + adaptive 3/5/7 w F6.4) ────────────────────

_THREAD_TEMPLATE = """=== PODSUMOWANIE DNIA GPW | {data} ===

--- NAJWAŻNIEJSZE OGŁOSZENIA ---
{top_ogłoszenia}

--- AKTYWNE SEKTORY ---
{sektory}

--- TRENDY I WZORCE ---
{trendy}

--- RYZYKA RYNKOWE ---
{ryzyka}

--- SZANSE RYNKOWE ---
{szanse}

{suggestions_context}=== ZADANIE ===
Wygeneruj WĄTEK (thread): 3, 5 lub 7 postów (adaptywnie do liczby mocnych newsów).
WAŻNE: Post dotyczy dnia {data} (D-1). NIE pisz "dziś/dzisiaj" — używaj "{data_short}".

LIMITY (X 2026 — twarde):
- KAŻDY post ≤ 280 znaków (idealne 240-270).
- MAX 1 cashtag $TICKER per post (jedna spółka per post = 1 cashtag).
- MAX 1 hashtag per post (zalecane #GPW w ostatnim).
- Brak linków URL.

STRUKTURA WĄTKU:
Post 1/N — HOOK „mini-zastrzyk konkretu" (BEZ cashtaga, BEZ hashtaga):

  ZASADY HOOK:
  - Wybierz 3 NAJMOCNIEJSZE fakty z DANYCH (najwyższe kwoty, najmocniejsze
    zdarzenia: upadłość, dywidenda XX zł, kontrakt XXX mln, strata netto).
  - BEZ nazw spółek w hooku — tylko fakty/liczby, żeby intrygować.
  - Format zależy od liczby spółek w threadzie (N = liczba spółek poniżej):

  GDY N > 3 (są dodatkowe spółki — cliffhanger ma sens):
  Format: "[3 fakty]. To tylko 3 z N newsów GPW z {data_short}. 🧵"
  ✅ "Upadłość, dywidenda 20 zł, kontrakt 915 mln zł. To tylko 3 z 6 newsów GPW z {data_short}. 🧵"
  ✅ "Wniosek o upadłość, +166% zysku, kontrakt z PKP PLK. 3 z 5 newsów {data_short}. 🧵"

  GDY N ≤ 3 (wszystkie pokazane — bez "tylko 3 z 3"!):
  Format: "[3 fakty]. Najważniejsze newsy GPW z {data_short}. 🧵"
  LUB krócej: "[3 fakty z {data_short}]. 🧵"
  ✅ "Anulowanie likwidacji, dywidenda 0,30 zł, strata netto 22 mln zł. Najważniejsze newsy GPW z {data_short}. 🧵"
  ✅ "Wniosek o upadłość, wzrost udziału 7,94%, emisja 38 mln akcji — GPW {data_short}. 🧵"

  ZAKAZANE HOOK:
  ❌ "To tylko 3 z 3 newsów" (jak N=3 — bez sensu, wszystko już pokazane)
  ❌ "5 newsów warto znać z {data_short}." (bez konkretu)
  ❌ "Najważniejsze ogłoszenia GPW" (filler bez liczb/faktów)
  ❌ "Sprawdź podsumowanie dnia" (CTA bez wartości)

Post 2..N-1 — JEDNA spółka per post (1 cashtag):
  [emoji sektora] $TICKER — fakt z danych + 1 zdanie kontekstu.
  Drugą spółkę w tym samym poście wymień plain text BEZ $.

Post N/N (ostatni) — CLOSE:
  Krótkie pytanie zamykające (zachęta do reply — algorytm waży reply ×27).
  Stopka prawna TYLKO TUTAJ:
  #GPW
  ⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.

Przykład poprawnego JSON:
{{"is_thread": true, "tweets": [
  "Upadłość, dywidenda 20 zł, kontrakt 915 mln zł. To tylko 3 z 5 newsów GPW z {data_short}. 🧵",
  "🏦 $PKO — zysk netto 1.2 mld PLN w Q1, +18% r/r.",
  "⚡ $PGE — kontrakt OZE 500 MW podpisany.",
  "Co Twoim zdaniem najmocniej ruszy kursami w pn?\\n#GPW\\n⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko."
]}}"""


# ── Template: WĄTEK CASHTAG V2 (2026-04-28+) ──────────────────────────────────
# Cashtag-heavy format wynikły z analizy viralowego tweeta 2026-04-28.
# Hook MUSI mieć cashtagi spółek (nie "BEZ cashtaga" jak w starym _THREAD_TEMPLATE).
# Body: bullet list 4-5 punktów (lista NIE narracja), max 500 zn.
# Closing: lista TOP movers + emoji (🚀⚠️🟢🔴) + cashtagi + disclaimer pełny.
#
# Używane TYLKO przez okna w XPOST_CASHTAG_V2_WINDOWS (config.py).
# Defense-in-depth: compliance guard nadal max 1 cashtag/post — multi-cashtag
# = email-only mode (X_AUTO_PUBLISH=false zablokuje publikację automatyczną).

_THREAD_TEMPLATE_V2 = """=== PODSUMOWANIE GPW {okno} | {data} ===

--- NAJWAŻNIEJSZE OGŁOSZENIA ---
{top_ogłoszenia}

--- AKTYWNE SEKTORY ---
{sektory}

--- TRENDY I WZORCE ---
{trendy}

--- RYZYKA RYNKOWE ---
{ryzyka}

--- SZANSE RYNKOWE ---
{szanse}

{cashtag_instructions}

{suggestions_context}=== ZADANIE ===
Wygeneruj WĄTEK 3-5 postów (adaptywnie do liczby spółek).
WAŻNE: Post dotyczy dnia {data} (D-1). NIE pisz "dziś/dzisiaj" — używaj "{data_short}".

LIMITY DŁUGOŚCI (Premium-light, 2026-04-28+):
- HOOK (1/N): ≤280 zn (skanowalny mobile, pierwszy frame).
- Body (2..N-1): 200-450 zn sweet spot, ≤500 zn hard. NIE wall-of-text!
  Forma: bullet list `• punkt 1\\n• punkt 2\\n• punkt 3` — strukturalnie.
- CLOSING (N/N): 150-380 zn, ≤400 zn hard. Lista cashtagów + emoji + disclaimer.

STRUKTURA WĄTKU:
Post 1/N — HOOK MUSI mieć cashtagi (TIER 1 z briefingu — najwięcej impressions):
  Format: "[$TICKER fakt 1, $TICKER fakt 2 z liczbą PLN]\\n\\n[krótkie wprowadzenie do nitki] 🧵"
  ✅ "$MDG konwertuje 16,52 mln zł długu na akcje\\n$JWW ⚠️ -7,57 mld PLN przepływów\\n\\nNajważniejsze ogłoszenia GPW {data_short} 🧵"
  ❌ "Konwersja długu i ujemne przepływy. Najważniejsze GPW 🧵" (bez cashtagów = strata zasięgu!)

Post 2..N-1 — JEDNA spółka per post, BULLET LIST format:
  [emoji sektora] $TICKER — [krótki nagłówek]:

  • [punkt 1 z liczbą PLN]
  • [punkt 2]
  • [punkt 3]
  • [punkt 4]

  [1 zdanie podsumowania z 2× $TICKER jeśli naturalnie pasuje]

Post N/N — CLOSING jako LISTA RANKINGOWA (NIE narracja!):
  🏆 [Tytuł: Ogłoszenia/Top movers/Co dzisiaj] {data_short}:

  [emoji 🚀 jeśli zysk >100% / ⚠️ strata <-10% / 🟢 standardowy zysk / 🔴 standardowa strata] $TICKER — [krótki opis lub PLN]
  [...kolejne pozycje, sweet spot 5-7 cashtagów]

  [krótkie pytanie zamykające jeśli pasuje]

  #GPW
  ⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.

PRZYKŁAD POPRAWNEGO JSON (3-tweet thread, 2 spółki):
{{"is_thread": true, "tweets": [
  "$MDG konwertuje 16,52 mln zł długu na akcje\\n$JWW ⚠️ -7,57 mld PLN przepływów operacyjnych\\n\\nOgłoszenia GPW {data_short} 🧵",
  "🩺 $MDG — aneks z BioFund:\\n\\n• Oprocentowanie: 18,5% → 14%\\n• Prowizja uchylona\\n• Konwersja 16,52 mln zł → akcje po 33,00 zł\\n• Lock-up BioFund do 20.01.2027\\n\\n$MDG zyskuje płynność, BioFund w akcjonariacie.",
  "🏆 Ogłoszenia {data_short} GPW:\\n\\n🟢 $MDG — konwersja długu 16,52 mln zł na akcje\\n⚠️ $JWW — przepływy operacyjne -7,57 mld PLN\\n\\nKtóre istotniejsze dla rynku?\\n\\n#GPW\\n⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko."
]}}"""


# ── Template: index_daily (thread, każdy post o jednym indeksie/spółce) ───────

_INDEX_DAILY_TEMPLATE = """=== GPW INDEKSY | {data} ===

{index_data}

=== ZADANIE ===
Wygeneruj WĄTEK 3-5 postów (każdy ≤ 280 znaków).

LIMITY (X 2026):
- KAŻDY post ≤ 280 znaków.
- MAX 1 cashtag $TICKER per post.
- MAX 1 hashtag per post.

STRUKTURA:
Post 1 — HOOK (bez cashtag/hashtag):
  Liczba + fakt z indeksów. Np.: "WIG20 +1.2% — 3 spółki ruszyły kursami {data_short}. 🧵"

Posty 2..N-1 — JEDNA spółka/post (1 cashtag):
  [emoji indeksu] $TICKER — 1-2 zwięzłe zdania z liczbami.
  🔵 = WIG20, 🟡 = mWIG40, 🟢 = sWIG80.

Ostatni post — CLOSE:
  Pytanie + #GPW + stopka.
  ⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.

Przykład poprawnego JSON:
{{"is_thread": true, "tweets": [
  "WIG20 +1.2% — 3 najmocniejsze spółki dnia z {data_short}. 🧵",
  "🔵 $PKO — zysk netto 1.2 mld PLN w Q1.",
  "🟡 $ASB — dywidenda 0.55 USD/akcję.",
  "Co najmocniej ruszy WIG20 jutro?\\n#GPW\\n⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko."
]}}"""


# ── Template: sobota (Thread tygodniowy — flagowiec) ─────────────────────────

_SATURDAY_TEMPLATE = """=== TYDZIEŃ NA GPW | {data_od}–{data_do} ===

--- NAJWAŻNIEJSZE OGŁOSZENIA TYGODNIA ---
{top_ogłoszenia}

--- AKTYWNE SEKTORY ---
{sektory}

--- TRENDY TYGODNIA ---
{trendy}

--- RYZYKA ---
{ryzyka}

--- SZANSE ---
{szanse}

=== ZADANIE ===
Wygeneruj WĄTEK 5-8 postów podsumowujących tydzień (każdy ≤ 280 znaków).

LIMITY (X 2026 — TWARDE):
- KAŻDY post ≤ 275 znaków (zostaw 5 zn. buforu — escape characters).
- MAX 1 cashtag $TICKER per post.
- MAX 1 hashtag per post.

STRUKTURA:
Post 1 — HOOK (bez cashtag/hashtag):
  Liczby tygodnia: WIG %, top gainer/loser, liczba mocnych newsów.
  Przykład: "Tydzień {data_od_short}–{data_do_short}: WIG +1.8%, 4 najważniejsze newsy. 🧵"

Posty 2..N-1 — JEDNA spółka/post (1 cashtag):
  [emoji sektora] $TICKER — najmocniejszy fakt z tygodnia + 1 zdanie kontekstu.

Ostatni post — CLOSE:
  Top 3 sektory + 1 teza na nowy tydzień (bez rekomendacji) + pytanie.
  #GPW
  ⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.

Przykład poprawnego JSON:
{{"is_thread": true, "tweets": [
  "Tydzień {data_od_short}–{data_do_short}: WIG +1.8%, 4 mocne newsy. 🧵",
  "🏗️ $BUD — kontrakt 80 mln zł podpisany.",
  "🏦 $PKO — zysk +18% r/r w Q1.",
  "Top sektory: budownictwo, banki, energia. Co Was najbardziej ciekawi?\\n#GPW\\n⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko."
]}}"""


# ── Template: niedziela (makro & kontekst — krótszy thread, ≤280/post) ───────

_SUNDAY_TEMPLATE = """=== WEEKLY OUTLOOK | dane makro z {data_makro} ===

{agenda_block}

--- MAKRO INDEKSY ---
{indeksy}

--- MAKRO SUROWCE ---
{surowce}

--- MAKRO WALUTY ---
{waluty}

--- POLSKA MAKRO ---
Stopa referencyjna NBP: {stopa_ref}%
Inflacja CPI r/r: {inflacja}% (okres: {inflacja_okres})

=== ZADANIE ===
Wygeneruj **WĄTEK 7 LONG postów** (X PREMIUM long-form, max ~5000 znaków/post).
Format: 1 hook + 5 dni (pn–pt) + 1 close. Jeden dzień = jeden long post z PEŁNĄ
agendą tego dnia (wszystkie wydarzenia z sekcji AGENDA).

⚠️ X PREMIUM LIMITY (zluzowane vs free):
- Hook (Post 1): ≤280 zn (krótki, retweet-friendly, bez cashtag/hashtag)
- Long posts (2-6): 500-5000 zn each
- Close (Post 7): ≤280 zn
- Cashtagi: **MAX 1 per post (KAŻDY tweet)** — wybierz TOP 1 spółkę dnia, reszta plain text
- Hashtagi: max 5 per post (zalecane #GPW + #dywidendy w close)
- ⛔ ZAKAZ: nazwy z .PL / .COM / .SA / .EU w ŻADNEJ formie (np. OPONEO.PL = BŁĄD → użyj OPONEO).

STRUKTURA WĄTKU 7 long postów:

Post 1 — HOOK (≤280 zn, bez cashtag/hashtag):
  Sumaryczne liczby tygodnia. Np.:
  "Tydzień DD-DD.MM na GPW: X dywidend (top yield Y%), Z WZA, R raportów rocznych. Pełna agenda per dzień. 🧵"

Posty 2-6 — JEDEN DZIEŃ = JEDEN LONG POST (1500-4000 zn):
  Format każdego dnia (PRZYKŁAD pn 20.04):

  📆 1/5 PONIEDZIAŁEK 20.04 (12 wydarzeń)

  💰 Dywidendy (1):
  **$ING** — INGBSK. Dywidenda 26,71 zł/akcję (yield 6,17%). Dzień ustalenia prawa: pn 20.04.

  🏛️ WZA (3):
  MBFGROUP — ZWZA: przeznaczenie zysku 2025
  NWAI — ZWZA: przeznaczenie zysku 2025
  VRFACTORY — NWZA: podwyższenie kapitału

  📊 Wyniki spółek (8):
  11BIT, DEKPOL, DIAG, GTC, OZECAPITAL, SOLARINOV, TRIGGO, ZREMB — publikacja raportów rocznych 2025.

  ZASADY long postów:
  - **DOKŁADNIE 1 cashtag** **$TICKER** per long post — TYLKO TOP 1 spółka dnia
    (najmocniejszy katalizator: dywidenda yield ≥5%, WIG20/mWIG40 raport, lub WZA z dywidendą).
    Reszta NULL — markdown bold **$TICKER** użyj TYLKO raz na cały post.
    ⚠️ KRYTYCZNE: TICKER = KOD GPW z sekcji "WYMAGANE KODY GPW" wyżej.
    NIE używaj nazwy spółki jako cashtag ($BNPPPL = BŁĄD → użyj $BNP).
    NIE używaj $INGBSK / $TSGAMES / $CIGAMES — to są nazwy. Kody GPW: $ING / $TEN / $CIG.
  - **Wszystkie pozostałe spółki: plain text WIELKĄ LITERĄ** bez `$`
    (np. TAMEX, MBFGROUP, OPONEO, BNPPPL, INGBSK, CIGAMES).
  - ⛔ ZAKAZ suffixu .PL/.COM/.SA/.EU. OPONEO.PL = BŁĄD → OPONEO. SILVAIR-REGS = OK.
  - Grupowanie per typ: 💰 Dywidendy → 🏛️ WZA → 📊 Wyniki → 🔔 Inne
  - Każda linia: NAZWA — 1 zdanie opisu (max 120 zn)
  - Jeśli typ ma >15 spółek: pierwsze 15 jako pełna lista + "i N więcej"

Post 7 — CLOSE (≤280 zn):
  Krótkie zaproszenie + #GPW + disclaimer. Np.:
  "Pełna lista wydarzeń (X łącznie) — który dzień was najbardziej ciekawi?
  #GPW
  ⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI."

REGUŁY:
- Tylko DOKŁADNE dane z agendy/makro — zero halucynacji.
- Polski format liczb: przecinek dziesiętny (26,71 zł nie 26.71)
- Markdown bold **$TICKER** TYLKO dla top 3 spółek dnia (X Premium renderuje)
- Sekcja makro wciąż w hooku/close (krótko: WIG20 +X%, USD/PLN, Brent)

JSON: {{"is_thread": true, "tweets": ["hook","pn-long","wt-long","sr-long","cz-long","pt-long","close"]}}"""


# ── Template: cytaty (deprecated daily — wchłonięte do sobotniego threadu) ───

_QUOTES_TEMPLATE = """=== CYTATY DNIA GPW | {data} ===

--- CYTATY Z OGŁOSZEŃ ---
{quotes_list}

=== ZADANIE ===
Wygeneruj WĄTEK z cytatami dnia (każdy post ≤ 280 znaków, 1 cytat/post).

LIMITY (X 2026):
- KAŻDY post ≤ 280 znaków.
- MAX 1 cashtag $TICKER per post (spółka której dotyczy cytat).
- MAX 1 hashtag (zalecane #GPW w ostatnim).

STRUKTURA:
Post 1 — HOOK (bez cashtag/hashtag):
  Liczba cytatów + fakt. Np.: "5 cytatów wartych znania z {data_short}. 🧵"

Posty 2..N-1 — 1 cytat per post:
  $TICKER
  „dosłowny cytat z ogłoszenia — bez parafrazowania"

Ostatni post — CLOSE:
  Pytanie + #GPW + stopka.
  ⚖️ Nie stanowi rekomendacji inwestycyjnej. Źródło: ESPI/EBI. Inwestujesz na własne ryzyko.

Cytaty TYLKO w cudzysłowie polskim „..." DOSŁOWNIE z ogłoszenia.
ZERO interpretacji — tylko cytaty.

JSON: {{"is_thread": true, "tweets": ["t1", "t2", ...]}}"""


# ── Template: broker decisions (poniedziałek 10:30 — F7.2) ───────────────────
# Decyzje EKSPERYMENTALNYCH portfeli AI (Standard + Short) za miniony tydzień.
# Publikowane PO otwarciu sesji żeby user już wykonał transakcje wg AI.
# Compliance: PAST tense, framing "eksperymentalne portfele", ZERO rekomendacji.

_BROKER_DECISIONS_TEMPLATE = """=== DECYZJE EKSPERYMENTALNYCH PORTFELI AI | {data_post} ===

Tydzień analizowany: {week_from}–{week_to}
Nastrój rynku (wg AI): {market_sentiment}

--- DECYZJE PORTFELA STANDARD (średnio-długoterminowy) ---
{decisions_standard}

--- DECYZJE PORTFELA SHORT (krótkoterminowy) ---
{decisions_short}

{required_block}

=== ZADANIE ===
Wygeneruj WĄTEK 4-6 postów z PODSUMOWANIEM decyzji eksperymentalnych portfeli AI.

⚠️ COMPLIANCE — BEZWZGLĘDNE ZASADY (X TOS + finansowe):
1. ❌ ZAKAZ słów: "kupuj", "sprzedaj", "warto", "rekomend*", "polec*"
   → To są DECYZJE eksperymentalnych AI brokerów, NIE rady dla followersów.
2. ✅ PAST tense ZAWSZE: "Żółw KUPIŁ" / "Zając SPRZEDAŁ" / "AI ZDECYDOWAŁA"
   → NIE "kupi" / "warto kupić" / "polecam zakup"
3. ✅ Framing: "eksperymentalny portfel AI" / "🐢 Standard / 🐇 Short"
4. ✅ Każda decyzja MUSI mieć cashtag z listy WYMAGANYCH KODÓW GPW.
5. ✅ OBOWIĄZKOWY disclaimer w ostatnim poście:
   "⚖️ Nie stanowi rekomendacji inwestycyjnej. Eksperymentalne portfele AI."

LIMITY (X 2026):
- KAŻDY post ≤ 275 znaków (5 zn. buforu).
- MAX 1 cashtag $TICKER per post.
- MAX 1 hashtag per post.

STRUKTURA (Thread 4-6):
Post 1 — HOOK (bez cashtag/hashtag):
  Liczba decyzji + nastrój rynku + cliffhanger.
  ⚠️ ZAKAZ użycia angielskiego "sentiment" / polskiego "s-e-n-t-y-m-e-n-t"
     (forbidden keyword w validatorze, hard-fail). Używaj synonimu:
     'nastrój', 'klimat', 'kondycja', 'tonacja', 'nastawienie'.
  Przykład: "5 decyzji eksperymentalnych portfeli AI po tygodniu na GPW. Nastrój: {market_sentiment}. 🧵"

Posty 2..N-1 — 1 DECYZJA / post (1 cashtag):
  [emoji portfel] [Standard 🐢 / Short 🐇] — [PAST verb] $TICKER za [kwota] PLN ([conviction]).
  Powód (1 zdanie z reasoning, bez słów zakazanych).

Ostatni post — CLOSE:
  Krótka teza tygodnia od AI + pytanie + #GPW + obowiązkowy disclaimer.

Przykład poprawnego JSON:
{{"is_thread": true, "tweets": [
  "5 decyzji eksperymentalnych portfeli AI z tygodnia 13.04-17.04. 🧵",
  "🐢 Standard — KUPIŁ $LWB za 500 PLN (conviction WYSOKA). Wzrost cen węgla + zapowiedź dywidendy.",
  "🐢 Standard — SPRZEDAŁ $CMP — pogarszające się fundamenty Q1 2026.",
  "🐇 Short — KUPIŁ $CDR za 800 PLN (SREDNIA). Premiera DLC w Q3 2026 jako katalizator.",
  "Eksperymentalne portfele AI testują strategie. Co Was ciekawi w tygodniu?\\n#GPW\\n⚖️ Nie stanowi rekomendacji inwestycyjnej. Eksperymentalne portfele AI."
]}}"""
