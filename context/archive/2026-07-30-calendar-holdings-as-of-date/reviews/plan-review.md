<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Holdings as of each day — kalendarz i wykres wartości

- **Plan**: `context/changes/calendar-holdings-as-of-date/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-31
- **Verdict**: REVISE → **SOUND** (po naniesieniu poprawek)
- **Findings**: 2 critical, 1 warning, 1 observation — wszystkie FIXED

## Verdicts

| Dimension | Verdict (przed) | Verdict (po) |
|-----------|-----------------|--------------|
| End-State Alignment | FAIL | PASS |
| Lean Execution | PASS | PASS |
| Architectural Fitness | PASS | PASS |
| Blind Spots | FAIL | PASS |
| Plan Completeness | WARNING | PASS |

Rubryka przy dwóch FAIL-ach wskazywała RETHINK. Nie zastosowano: rdzeń podejścia (korekta
wsteczna nad snapshotem) jest potwierdzony pomiarem na produkcji, a oba FAIL-e były błędami
specyfikacji w treści dwóch faz, nie wadą metody.

## Grounding

8/8 ścieżek ✓, symbole ✓ (`CROSS JOIN positions` ×2 w `db/bigquery.py`; `QUALIFY ROW_NUMBER`
obecny w wykresie `:582`, nieobecny w kalendarzu), Progress 5/5 faz i 36/36 kryteriów ✓,
0 checkboxów poza sekcją Progress ✓, brief↔plan ✓

Weryfikacja na realnym BigQuery (2026-07-31):

```
ops_days_off_spine   16
trade_days_off_spine  0
total_op_days       426

portfolio_id   first_op     first_buy
6c6fdd5b…      2025-01-28   2025-01-29
d49d0121…      2025-01-28   2025-01-29
10414536…      2025-07-09   2025-07-10
57ed5830…      2025-07-09   2025-07-10
```

## Findings

### F1 — Okno korekty wstecznej urywa się na końcu miesiąca

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Blind Spots
- **Location**: Faza 1 — CTE `holdings`; „Critical Implementation Details"
- **Detail**: Okno `ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING` sumuje wyłącznie
  wiersze obecne w osi, a oś kalendarza to `lookback_start..end_date` — jeden miesiąc. Dla
  miesiąca z przeszłości `UNBOUNDED FOLLOWING` kończy się na jego ostatnim dniu, więc zakup
  dokonany później nie zostanie odjęty: czerwiec 2025 obejrzany dziś nadal policzy akcje
  kupione w grudniu 2025. Plan sam sobie przeczył — „do końca miesiąca" i „aż do dziś"
  w jednym zdaniu. Druga, uśpiona ścieżka awarii: join operacji do osi po równości dat gubi
  operacje z dni spoza osi; zmierzone 16 z 426 dni operacyjnych, dziś wyłącznie niehandlowych.
- **Fix A ⭐ Recommended**: różnica sum kumulacyjnych zamiast okna nad osią —
  `today_shares − (total_signed − cum_signed_through_day)`, warunek zakresu `op_date <= day`.
  - Strength: zamyka obie ścieżki naraz; odporne na przyszłą rekonstrukcję gotówki.
  - Tradeoff: range join, o jedno CTE dłużej.
  - Confidence: HIGH — horyzont analitycznie, 16/426 pomiarem.
  - Blind spot: plan wykonania range joina na BQ niezmierzony.
- **Fix B**: rozszerzyć oś operacji do `CURRENT_DATE`, zachować okno.
  - Strength: mniejszy diff.
  - Tradeoff: nie naprawia operacji spoza osi; dwie osie do utrzymania.
  - Confidence: MED.
- **Decision**: FIXED via Fix A

### F2 — Granica z pierwszej operacji produkuje fałszywe „0 PLN"

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Faza 2 — CTE granicy
- **Detail**: Plan definiował inception jako `MIN(DATE(occurred_at))`. Na produkcji pierwszą
  operacją każdego portfela jest wpłata, nie zakup (Główny 2025-01-28 vs 2025-01-29; IKZE
  2025-07-09 vs 2025-07-10). Dzień wpłaty przechodzi więc przez granicę, a jedynym
  posiadaniem jest wtedy reszta gotówkowa — wyceniona po 1,00 z `daily_chg = 0`. Daje to
  `prices_found = 1 > 0`, czyli stan `data` (`src/portfolio_calendar.py:104-109`) i komórkę
  „0 PLN" renderowaną jako realny płaski dzień. Plan nie spełniał własnego kryterium 2.5,
  a bliźniaczy portfel bez zaimportowanej gotówki wyrenderowałby ten dzień inaczej — łamiąc
  także kryterium 5.6.
- **Fix A ⭐ Recommended**: inception = pierwsza operacja zmieniająca stan posiadania
  (`op_type IN ('buy','sell')`), fallback bez zmian.
  - Strength: spełnia kryterium 2.5 dosłownie, usuwa rozjazd bliźniaków, zgodne z treścią
    ticketu.
  - Tradeoff: okres „wpłaciłem, jeszcze nie kupiłem" znika z widoku.
  - Confidence: HIGH — daty zmierzone, mechanika prześledzona do renderera.
  - Blind spot: portfel z samymi wpłatami dostaje pusty kalendarz — udokumentowane w planie.
- **Fix B**: inception z pierwszej operacji + tłumienie dni z samą gotówką.
  - Strength: zachowuje fakt istnienia portfela.
  - Tradeoff: warunek do utrzymania w obu zapytaniach za tę samą widoczną korzyść.
  - Confidence: MED.
- **Decision**: FIXED via Fix A

### F3 — Faza 3 nie przenosi progu pyłu na siatkę wykresu

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Completeness
- **Location**: Faza 3 — punkt 1
- **Detail**: Faza 1 jawnie odfiltrowuje `ABS(shares_on_day) <= 1e-9`; Faza 3 progu nie
  powtarzała. Bez niego ticker o zerowym stanie zostaje w `filled` z niezerowym `px_ff`
  i wchodzi do `COUNTIF(px_ff IS NOT NULL) AS covered` — bramka przestaje znaczyć „nic nie
  da się wycenić", zaczyna znaczyć „nic nie ma w uniwersum". Skutek poboczny: `notes`
  zgłosiłoby debiut tickera, którego wtedy nie posiadano.
- **Fix**: powtórzyć próg pyłu w `grid`, liczyć `covered` wyłącznie po niezerowych akcjach,
  dodać trzecią asercję do testu rozłączności z BOCF.
- **Decision**: FIXED

### F4 — Portfel mieszany dostaje granicę z operacji

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Faza 2; brief — „Open Risks"
- **Detail**: Fallback odpala się tylko przy całkowitym braku operacji zakupu/sprzedaży, więc
  portfel z importem plus pozycją dodaną ręcznie dostanie granicę z operacji i będzie trzymał
  tę pozycję stałą potencjalnie o półtora roku za wcześnie. Na produkcji taki portfel dziś
  nie istnieje.
- **Fix**: dopisać jako świadomie przyjęte ograniczenie do Fazy 2 i „Open Risks" w briefie,
  bez zmiany zachowania.
- **Decision**: FIXED

## Zmiany naniesione w planie

- Faza 1: `ops_totals` + `holdings` przepisane na różnicę sum kumulacyjnych; oba tryby awarii
  udokumentowane z pomiarem.
- „Critical Implementation Details": sprzeczne zdanie o horyzoncie skanu operacji poprawione.
- Faza 1, testy: trzy strażniki negatywne (`CROSS JOIN positions`, `ROWS BETWEEN 1 FOLLOWING`,
  `o.op_date = `).
- Faza 2: granica zawężona do `op_type IN ('buy','sell')` z uzasadnieniem i pomiarem;
  udokumentowane dwa skutki uboczne (portfel bez zakupów, portfel mieszany).
- Faza 3: próg pyłu i liczenie `covered`; rozróżnienie „wypada przez granicę" vs „przez bramkę";
  trzecia asercja w teście rozłączności.
- Faza 4: dwie nowe asercje round-tripu (miesiąc z przeszłości nie widzi późniejszego zakupu;
  operacja z dnia spoza osi nie ginie) plus asercja na dzień wpłaty.
- Daty IKZE poprawione z 2025-07-09 na 2025-07-10 w End State, kryteriach 2.6 i 5.5 oraz
  w sekcji Progress.
- Brief: dwa nowe wiersze w „Key Decisions", zaktualizowana sekcja „Architecture", nowe
  ryzyko w „Open Risks".
