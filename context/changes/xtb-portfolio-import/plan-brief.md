# Import pozycji i dywidend z eksportu XTB — Plan Brief

> Pełny plan: `context/changes/xtb-portfolio-import/plan.md`
> Research: `context/changes/xtb-portfolio-import/research.md`
> Analiza plików źródłowych: `context/changes/xtb-portfolio-import/change.md`

## What & Why

Ręczne wpisywanie pozycji do Mojego portfela jest wolne i podatne na błędy — dowodem jest
CBF wpisany z ceną 199,40 zamiast 188,40. Broker już eksportuje wszystkie dane, więc import
pliku sprowadza wprowadzenie portfela do jednego kliknięcia i przy okazji koryguje literówki.
Dodatkowo eksport niesie komplet dywidend gotówkowych, których aplikacja dziś w ogóle nie zna.

## Starting Point

Aplikacja przechowuje jeden wiersz na `(portfel, ticker)` z pojedynczym skalarem
`avg_buy_price`. Upload pliku nie istnieje nigdzie — ani we froncie, ani w backendzie —
a `python-multipart` i czytnik xlsx nie są zainstalowane. Nie ma magazynu dywidend
ani żadnego dopasowywania lotów. Za to istnieją gotowe do ponownego użycia: MERGE
insert-only z deduplikacją partii, szablon modalu z dropdownem oraz wzorzec koperty
odpowiedzi niosącej zastrzeżenia razem z danymi.

## Desired End State

Użytkownik klika **Importuj** obok Eksport CSV, wybiera dom maklerski i plik, i widzi podgląd:
co zostanie zapisane, co usunięte jako sprzedane, czego aplikacja nie rozpoznała, co zostanie
nietknięte, oraz ile nowych dywidend przybędzie. Po potwierdzeniu wszystkie widoki — łącznie
z domyślną zakładką „Wszystkie" — natychmiast pokazują aktualny stan. Pod Tabelą pojawia się
czwarta zakładka **Dywidendy** z wyborem roku, kafelkami brutto/podatek/netto i rozbiciem
na spółki.

## Key Decisions Made

| Decyzja | Wybór | Dlaczego | Źródło |
| --- | --- | --- | --- |
| Model danych | Surowe operacje jako źródło prawdy; pozycje i dywidendy jako projekcje | Deduplikacja po ID brokera działa jednolicie, a daty transakcji zostają zapisane bez kolejnej migracji | Plan |
| Klucz deduplikacji | Kolumna `ID` z eksportu, jako `xtb:{ID}` | Wypełniona w 571/571 operacji, bez kolizji między plikami — hasz treści zbędny | change.md |
| Metoda średniej ceny | FIFO po pozostałych lotach | Odtwarza to, co XTB pokazuje w UI, co do grosza na 19 z 20 pozycji | change.md |
| Pozycje nieobecne w pliku | Nietknięte, pokazane w podglądzie | S2B to realne 272,80 zł nabyte jako dywidenda rzeczowa, strukturalnie nieobecna w eksporcie | change.md |
| Tickery zamknięte w pliku | Usuwane, z jawnym ujawnieniem w podglądzie | Plik jednoznacznie stwierdza sprzedaż całości — zostawienie pozycji zafałszowałoby wartość | Plan |
| Potwierdzenie | Wszystko albo nic, z pięcioma sekcjami ujawnienia | Zgodne z filozofią PUL-100; eksport brokera i tak nie jest edytowalny | Plan |
| Instrumenty zagraniczne | Pomijane w parserze | 11 operacji, wszystkie na pozycjach zamkniętych, **zero dywidend** — pominięcie nie rusza żadnej sumy | Plan |
| Transport pliku | `UploadFile` + `python-multipart` | Idiomatyczne dla FastAPI; zależność musi wejść tym samym commitem, bo brak wywala start serwisu | Research |
| Widok dywidend | Czwarta zakładka `data-mode` | Cztery miejsca do zmiany zamiast pięciu plus routing | Research |
| Dziury w cache | Naprawiane przy okazji | Mała poprawka, która naprawia też dzisiejszy zapis pojedynczej pozycji | Research |
| Weryfikacja | Złoty zbiór w teście plus podgląd jako dry-run na prodzie | Znamy oczekiwany wynik co do grosza — błąd w FIFO wyjdzie liczbowo, nie po cichu | Plan |

## Scope

**W zakresie:** parser XTB z rejestrem brokerów · silnik FIFO · tabela operacji w BigQuery
z idempotentnym zapisem · endpointy podglądu, commitu i dywidend · naprawa unieważniania
cache · modal importu · zakładka Dywidendy · weryfikacja produkcyjna

**Poza zakresem:** instrumenty zagraniczne · dywidendy rzeczowe (spin-offy) · usunięcie
przybliżenia z wykresu wartości · zrealizowany P/L · drugi dom maklerski · konsolidacja
pięciu istniejących wzorców modalnych

## Architecture / Approach

Warstwa po warstwie, od środka na zewnątrz. Parser jest czystą funkcją bez BigQuery i HTTP,
zgodnie z precedensem `src/portfolio_treemap.py` — dzięki temu złoty zbiór weryfikuje się
zanim powstanie jakikolwiek endpoint.

```
plik .xlsx ──> src/brokers/xtb.py (czysty)  ──> operacje ──> FIFO ──> pozycje
                                              └──────────────────────> dywidendy
                       │
                       ▼
        POST /import/preview  (nic nie zapisuje)
        POST /import/commit   (3 zapytania BQ, nie pętla)
                       │
                       ▼
        user_broker_operations  ──projekcje──>  pozycje + zakładka Dywidendy
```

Podgląd i commit są **bezstanowe** — commit przyjmuje ten sam plik ponownie i parsuje go
od nowa. Powód jest konkretny: cache jest per proces, a Cloud Run chodzi z dwiema instancjami,
więc token podglądu zapisany przez jedną instancję bywałby nieznany drugiej.

## Phases at a Glance

| Faza | Co dostarcza | Główne ryzyko |
| --- | --- | --- |
| 1. Parser i FIFO | Czysty moduł z rejestrem brokerów, złoty zbiór w testach | Błędne czytanie fill-ów `9/11` zdublowałoby wolumen |
| 2. Warstwa BigQuery | Tabela operacji, idempotentny MERGE, agregacja dywidend | Klastrowanie jest nieodwracalne; mockowane testy nie parsują SQL |
| 3. API | Upload, podgląd, commit, dywidendy, naprawa cache | Brak `python-multipart` wywala start całego serwisu, nie pojedyncze żądanie |
| 4. Front: modal importu | Przycisk, okno, podgląd, commit | Przycisk musi zniknąć w trybie „Wszystkie"; cztery cache frontowe do wyczyszczenia |
| 5. Front: Dywidendy | Czwarta zakładka z rokiem i rozbiciem na spółki | Cztery jawne miejsca ukrywania paneli — pominięcie zostawia panel widoczny |
| 6. Weryfikacja produkcyjna | Import obu realnych plików, dowód idempotencji | Usunięcie tickerów zamkniętych to jedyna operacja bez łatwego cofnięcia |

**Prerequisites:** dostęp do BigQuery przez ADC do round-tripu · oba pliki eksportu
w `export_xtb/` (poza repo) · uprawnienia do merge'a na `master` (deploy jest automatyczny)

**Estimated effort:** ~4–6 sesji; fazy 1–2 są niezależne od UI i dają się wykonać jednym ciągiem

## Open Risks & Assumptions

- **Klastrowanie tabeli jest nieodwracalne.** `ensure_schema_current` potrafi wyłącznie
  doklejać kolumny — zmiana klastrowania oznaczałaby ręczne odtworzenie tabeli.
- **Usuwanie tickerów zamkniętych to jedyna niszcząca ścieżka importu.** Przy Twoich obecnych
  danych nie wystąpi ani razu, ale przed pierwszym importem produkcyjnym warto zrzucić
  `user_portfolio_positions` jako punkt powrotu.
- **Cache jest per proces przy dwóch instancjach Cloud Run.** Zapis obsłużony przez jedną
  instancję nie unieważni kopii drugiej. Własność istniejąca, nie zmieniana tym planem,
  ale bardziej widoczna przy przepływie „potwierdź i od razu czytaj".
- **Zakładamy, że format eksportu XTB jest stabilny.** Mapowanie kolumn po nagłówku
  z głośnym błędem przy braku kolumny jest zabezpieczeniem, ale zmiana etykiet po stronie
  brokera wymusi poprawkę.
- **Dywidendy rzeczowe pozostaną poza sumami.** Shoper i Cyber_Folks trafią do portfela
  tą samą drogą co S2B — ręcznie. Nazwa „dywidendy gotówkowe" jest świadoma.

## Success Criteria (Summary)

- Import realnego pliku Głównego daje 12 pozycji zgodnych z wyrocznią, koryguje CBF
  ze 199,40 na 188,40 i zostawia S2B nietknięty
- Zakładka Dywidendy pokazuje 1 015,23 zł netto za 2025 w Głównym i zerowy podatek w IKZE,
  bez sprawiania wrażenia błędu
- Powtórny import tego samego pliku raportuje zero nowych operacji i nie zmienia niczego
