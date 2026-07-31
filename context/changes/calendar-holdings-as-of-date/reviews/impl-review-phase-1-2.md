<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Holdings as of each day — fazy 1-2

- **Plan**: `context/changes/calendar-holdings-as-of-date/plan.md`
- **Scope**: Fazy 1-2 z 5
- **Date**: 2026-07-31
- **Verdict**: REJECTED → **APPROVED** (po naniesieniu poprawek)
- **Findings**: 1 critical, 3 warnings, 3 observations — wszystkie rozstrzygnięte

## Verdicts

| Dimension | Verdict (przed) | Verdict (po) |
|-----------|-----------------|--------------|
| Plan Adherence | WARNING | PASS |
| Scope Discipline | PASS | PASS |
| Safety & Quality | FAIL | PASS |
| Architecture | WARNING | PASS |
| Pattern Consistency | WARNING | PASS |
| Success Criteria | WARNING | PASS |

Kryteria automatyczne: 292 zielone (BQ/API/ETF), 792 zielone (szybka pętla), `tach check` OK.
Harness round-trip: 10/10 sprawdzeń kalendarza przechodzi; zatrzymuje się na `_check_history`,
co jest zakresem Fazy 3.

## Findings

### F1 — `ABS()` w progu pyłu przepuszcza realnie ujemne akcje

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM
- **Dimension**: Safety & Quality
- **Location**: `db/bigquery.py:555`
- **Detail**: `ABS(shares_on_day) > 1e-9` uzasadniony pyłem 1e-13, ale przepuszczał też
  realnie ujemny stan, który wchodził do `SUM(shares * close_ff)` jako ujemny wkład.
  Ścieżka: ticker z zakupami bez wiersza pozycji → `0 − (N − 0) = −N` dla każdego dnia
  przed zakupem. Wytwarza to `DELETE /api/portfolio/positions/{ticker}` (`src/api.py:1062`),
  które kasuje pozycję i zostawia operacje. Zweryfikowane na produkcji: **0 takich tickerów
  dziś** — 40 tickerów bez pozycji to walory sprzedane do zera (`total_signed = 0`),
  obsłużone poprawnie. Defekt latentny, o jedno kliknięcie od uruchomienia.
- **Fix**: `ABS(hd.shares_on_day) > 1e-9` → `hd.shares_on_day > 1e-9`, z komentarzem
  wyjaśniającym, czego próg broni. Żaden przypadek brzegowy z harnessu nie ucierpiał:
  oversell rekonstruuje się dodatnio (3 − (−5) = 8), walor sprzedany do zera schodzi do 0.
- **Decision**: FIXED

### F2 — NULL `portfolio_id` rozszczepia ticker na dwa wiersze w trybie „Wszystkie"

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM
- **Dimension**: Architecture
- **Location**: `db/bigquery.py:516-517`
- **Detail**: Plan wymagał, by join „zniósł" NULLowalne `portfolio_id` (`db/bigquery.py:720`).
  `ON t.portfolio_id = p.portfolio_id` nigdy nie dopasuje NULL-a, więc osierocona pozycja
  (sprzed PUL-64) i operacje tego samego tickera dają dwa wiersze `holders` — wartości się
  znoszą, ale `prices_found` i `total_positions` się podwajają. Zweryfikowane: **0
  osieroconych pozycji na produkcji** (43 wiersze, wszystkie z portfelem). W trybie
  pojedynczego portfela filtr je odcina, więc dotyczy wyłącznie „Wszystkie".
- **Fix**: Uznane za rozwiązane przez F1 — wiersz ops-only rekonstruuje się ujemnie przed
  pierwszym zakupem i wypada przez dodatni próg, zostaje sama osierocona pozycja trzymana
  stała, czyli udokumentowana semantyka reszty. Dopisany komentarz przy joinie, żeby następny
  czytelnik nie szukał `IS NOT DISTINCT FROM`. Klucz NULL-safe odrzucony: scaliłby dwa byty,
  o których nic nie mówi, że są tym samym.
- **Decision**: FIXED (przez F1 + dokumentacja)

### F3 — Dedup cen mógł wybrać NULL zamiast realnego kursu

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: `db/bigquery.py:442-447`, `:449-455`
- **Detail**: Dedup dopisany „równając kalendarz do wykresu", ale wykres najpierw odfiltrowuje
  NULL-e (`AND kurs_zamkniecia IS NOT NULL`, `:698`, `:703`) i dopiero dedupuje. Kalendarz
  sortował po `fetched_at DESC` bez tego — a od PUL-98 feed realnie zapisuje wiersze bez
  kursu (100 z 332 wierszy NewConnect). Duplikat, którego świeższy wiersz ma NULL, wybrałby
  NULL i odrzucił realną cenę.
- **Fix**: `ORDER BY (kurs_zamkniecia IS NOT NULL) DESC, fetched_at DESC` w obu CTE.
  Kalendarz nie może skopiować filtra wykresu wprost, bo potrzebuje też `zmiana_kwotowa`.
- **Decision**: FIXED

### F4 — Asercja stałości reszty w harnessie była pusta

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Success Criteria
- **Location**: `scripts/test_bq_portfolio_time_dimension.py:262-263`
- **Detail**: `assert abs(march[day]["portfolio_value"] - (march[day]["portfolio_value"])) < _EPS`
  to `X − X < ε` — zawsze prawda. Było to jedyne sprawdzenie niezmiennika reszty.
- **Fix**: Izolacja wkładu gotówki przez odjęcie nogi akcyjnej, która różni się między dniami
  (10 akcji przed sprzedażą, 6 po), plus asercja na konkretną wartość 250,00.
- **Decision**: FIXED

### F5 — Granica liczyła `created_at` w UTC, operacje w Europe/Warsaw

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: `db/bigquery.py:502` vs `:469`
- **Detail**: Dwie derywacje daty w tym samym łańcuchu CTE w różnych strefach. Portfel
  założony po 23:00 czasu warszawskiego dostałby granicę o dzień za wcześnie.
- **Fix**: `MIN(DATE(created_at, 'Europe/Warsaw'))`.
- **Decision**: FIXED

### F6 — Nieudokumentowana konsekwencja: portfel z samymi wpłatami

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: `db/bigquery.py` docstring
- **Detail**: Plan (Faza 2) wprost prosił o udokumentowanie, że portfel bez ani jednego
  zakupu dostaje pusty kalendarz. Komentarz przy CTE tłumaczył *dlaczego* granica to pierwszy
  zakup, ale docstring w ogóle nie wspominał o granicy.
- **Fix**: Akapit o granicy, o wyborze „pierwszy zakup, nie pierwsza operacja" i o przyjętej
  konsekwencji dopisany do docstringu.
- **Decision**: FIXED

### F7 — Dzień po inception z samą gotówką nadal renderuje zielone „+0 PLN"

- **Severity**: 💡 OBSERVATION
- **Impact**: 🔎 MEDIUM
- **Dimension**: Architecture
- **Location**: `db/bigquery.py:545-546` → `src/portfolio_calendar.py:104-113`
- **Detail**: Granica ścina lewą krawędź. Portfel, który sprzedał wszystko i siedzi
  w gotówce, nadal emituje wiersz o zerowym ruchu → stan `data` → zielona komórka „+0 PLN".
  Zero jest tu **prawdą** (portfel istniał i był płaski), w odróżnieniu od dnia wpłaty. Ale
  pokazana wartość gotówki jest dzisiejsza, nie historyczna, bo gotówka jest czystą resztą.
  Na produkcji nieosiągalne dziś — żaden portfel nie wyzerował akcji.
- **Fix**: Zostawione świadomie, udokumentowane w docstringu wraz z powodem, dla którego
  gotówki nie da się odtworzyć (pominięte instrumenty zagraniczne: 84,03 z sumy brokera
  przeciw 143,94 z operacji).
- **Decision**: ACCEPTED (udokumentowane)

## Uwagi poza findingami

- **`tests/test_api.py` — planowana zmiana okazała się zbędna.** Plan (Faza 1, punkt 3)
  zakładał, że filtr portfela zniknie z CTE `positions` i złamie asercje na literał
  `AND portfolio_id = @portfolio_id`. Filtr trafił do trzech CTE w tej samej formie, więc
  literał został i testy przeszły bez zmian.
- **Asercje na tekst SQL są kruche z natury.** Dwie z nich padły po naniesieniu poprawek
  F1 i F5, bo pinowały dokładną pisownię. Przepisane na intencję. Warto pamiętać, że jedyną
  siatką na arytmetykę pozostaje harness round-trip — testy stringowe są dokumentacją decyzji,
  nie pokryciem.
- **`status` w `change.md` zostawiony jako `implementing`**, nie `impl_reviewed` — recenzja
  obejmuje 2 z 5 faz, a `/10x-tdd` wznawia się po tym polu.
