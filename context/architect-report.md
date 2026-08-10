---
title: Raport architektoniczny — moduł 4 (ścieżka 10xArchitect)
created: 2026-08-08
updated: 2026-08-10
author: Radek
type: architect-report
---

# Raport architektoniczny — moduł 4

## 1. Opisane projekty

**Jedno repozytorium dla wszystkich czterech artefaktów** — `puls-gpw`, mój projekt zaliczeniowy.
Artefakty łańcuchują się: mapa wskazała strefę ryzyka, research ją pogłębił, ranking wybrał z niej
kandydata, analiza domenowa odpowiedziała na pytania, przed którymi research się zatrzymał.

| | |
| -- | -- |
| **Produkt** | analizator komunikatów ESPI/EBI dla GPW i NewConnect: scraping → ocena przez Gemini → powiadomienia → portfel i watchlista |
| **Stack** | Python 3.13, FastAPI, BigQuery, Cloud Run, uv, Vertex/Gemini, Firebase Auth; front bez bundlera: `static/index.html` (6326 linii) + jeden pomocniczy `static/js/treemap-layout.js` |
| **Skala** | 500 commitów / 82 dni (2026-05-20 → 2026-08-10), jeden autor. `db/bigquery.py` 4493 linie, `static/index.html` 6326, `src/api.py` 1568 przy **32 trasach**. 1193 testy (997 jednostkowych + 196 e2e). **79 zamkniętych zmian w `context/archive/`** |
| **Gdzie się pojawia** | L2, L3, L4, L5 — wszędzie |

## 2. Mapa projektu (z L2)

Artefakt: `context/map/repo-map.md` + trzy artefakty robocze.

1. **Ryzyko jest skoncentrowane w jednym połączeniu i jest mierzalne.** `src/api.py` ściąga
   **51 nazw** z `db.bigquery`, więc testu nie da się podpiąć w jednym miejscu —
   `tests/e2e/conftest.py` łata **62 symbole** (`ast-grep` i `grep` zgodnie).
2. **Fundamentem grafu nie jest baza, tylko `src/exceptions.py`** (fan-in 11). Kształt „wąskiego
   dołu": najczęściej importowane są małe, bezstanowe moduły. Zero cykli.
3. **`src/api.py` jest ósmym punktem wejścia**, nie modułem logiki: fan-out 7, 32 trasy, zero
   własnej domeny.
4. **Frontend jest strukturalnie białą plamą** — 6326 linii bez modułów. To `unknown`, nie „brak
   powiązań"; stąd 208 dotknięć `tests/e2e`.
5. **Pamięcią projektu jest `context/`, nie kod** — przy bus factor 1 archiwum 79 zmian jest
   jedyną kopią zapasową kontekstu.

**Najważniejszy `unknown`:** graf pokazuje importy, nie wywołania, i nie zagląda do SQL-a
w stringach. Obie ślepe plamy zemściły się w L3.

## 3. Analiza ficzera (z L3)

Artefakt: `context/changes/portfolio-lots-flow/research.md`.

**Co badałem i dlaczego.** Przepływ FIFO-lotów portfela — przecinają się na nim trzy strefy ryzyka
mapy, a **jedyne nieudokumentowane naruszenie warstwy w repo dotyczy dokładnie tego modułu**:
`db/bigquery.py:43` importuje `src.portfolio_lots`, a komentarz w `tach.toml` wymienia trzy inne
krawędzie i o tej nie wie.

**Feature overview.** Dane wchodzą dwiema rozłącznymi drogami — ręczny wpis
(`POST /api/portfolio/positions`) i import brokerski (`/import/commit`) — i **nigdy się nie
spotykają w zapisie**. Import parsuje `.xlsx`, przepuszcza zdarzenia przez jedyną w repo księgę
FIFO (`src/portfolio_lots.py:145`) i wykonuje trzy zapisy do BigQuery. Stan zmienia się w dwóch
tabelach, a odpowiedź to liczniki, **nigdy nie odczytane ponownie z bazy**. Księga ma trzech
konsumentów: datę nabycia, podstawę kosztową i zysk zrealizowany.

**Technical debt — trzy najważniejsze.**

- **Dwie niezależne rekonstrukcje stanu posiadania w jednej funkcji.** `get_portfolio_history`
  odpowiada na „ile sztuk" SQL-em **wstecz** (`db/bigquery.py:944`), a na „po jakim koszcie"
  Pythonem FIFO **wprzód** (`:924`). Licznik i mianownik P&L z dwóch implementacji w dwóch
  językach. **Potwierdzone `ast-grep`.**
- **FIFO karmione niedeterministyczną kolejnością** — `ORDER BY occurred_at` bez klucza wtórnego
  (`db/bigquery.py:4153`, jedyne wystąpienie).
- **Adaptery bez siatki.** `list_broker_trades`, **jedyne wejście produkcyjne do przepływu**, ma
  zero testów (0 wywołań w `tests/`, same `patch`); `basis_segments` nie biegnie w e2e ani razu.

**Dwa `unknown` domknąłem pomiarem na produkcji** (czysty odczyt, bez DML):

| Pytanie | Wynik | Co z tego wynika |
| -- | -- | -- |
| Czy niezmiennik uzgadniający jest łamany dziś? | **1 ze 108** wierszy `holders`, 4 użytkowników | Rozjazd istnieje, ale to pozycja wpisana ręcznie, **bez operacji** — przypadek z definicji poprawny |
| Czy FIFO dostaje remisy w kolejności? | **2 grupy / 4 wiersze z 701** transakcji, **0** z różną ceną | Kolizja jest, szkody nie ma — ryzyko uśpione do pierwszego importu z różnymi cenami |

Pierwszy pomiar dał wniosek ostrzejszy niż samo „nie": `residual_holders` zlicza przypadek łagodny
**tą samą liczbą** co groźny i nie ma nigdzie zapisanej wartości bazowej. Alarm bez linii bazowej
nie jest alarmem — i to zmieniło kształt fazy 3 planu.

## 4. Plan refaktoryzacji (z L4)

Artefakty: `context/changes/refactor-opportunities/research.md` + `plan.md`.

**Co refaktoryzujemy:** domknięcie testowe szwu odczytu (`list_broker_trades`,
`_first_open_lot_dates`) plus tani guard przypinający tożsamość
`ledger_open(D) + residual == shares_on_day(D)`. **Docelowy kształt to osłona, nie zmiana
struktury** — obie pozycje mają werdykt *świadome ograniczenie*, więc obowiązuje reguła
„guard, nie przebudowa".

**Czego świadomie NIE robimy:** nie unifikujemy rekonstrukcji (rozdział jest wymuszony — FIFO nie
da się wyrazić w SQL); nie włączamy `tach` jako bramki (**`tach check` jest dziś zielony** —
deklaracja `db depends_on src` jest hurtowa, więc bramka nie złapałaby krawędzi będącej powodem
problemu); nie dodajemy `response_model` (nie ma go przy żadnej z 32 tras — konwencja repo, nie
odchył); nie wstrzykujemy repozytorium (32 trasy i 30 plików e2e naraz — kampania, nie plan).

| Faza | Weryfikacja |
| -- | -- |
| 1. Test `list_broker_trades` — pin filtrów, parametrów i kształtu wyniku | auto: bramka `Tests` · ręcznie: test musi **poczerwienieć** po usunięciu warunku z SQL-a |
| 2. `_first_open_lot_dates` na **niepustym** wejściu (dziś tylko `return_value=[]`) | auto: bramka `Tests` · ręcznie: czy nie duplikuje testów czystej funkcji |
| 3. Kontrola równoważności obu rekonstrukcji w istniejącym skrypcie round-trip | auto: test `ast`-owy pilnujący, że kontrola jest **podpięta do `main()`** · ręcznie: zepsuć jedną stronę i potwierdzić czerwień |
| 4. Egzekwowanie: zapis w `lessons.md`, że kontrola jest obowiązkowa | auto: test sprzęgający `lessons.md` ze skryptami, na które się powołuje · ręcznie: czy zapis mówi **kiedy** uruchomić |

Mechanizm ląduje na zielono w fazach 1–3; **egzekwowanie włącza się dopiero fazą 4**. Sama
równoważność rekonstrukcji jest dla pytesta nieosiągalna — strona SQL wykonuje się w BigQuery,
a zamockowana porównywałaby mock ze sobą. Automatyzowalne jest za to **okablowanie**: że
kontrola jest podpięta i że dokument nie wskazuje na skasowany mechanizm. Oba testy istnieją
i są dziś zielone (`tests/test_roundtrip_script_wiring.py`), więc kryterium automatyczne każdej
z czterech faz jest realne, nie deklaratywne.

## 5. Domena wg DDD (z L5)

Artefakty: `context/domain/01…03`.

**Ubiquitous language.** Produkt ma **dwie domeny, nie jedną**: analiza ESPI/EBI (**Core** — cała
wizja, 11 FR-ów i oba Success Criteria) oraz portfel (w `prd.md:150` jawny **Non-goal → v3+**).
Pojęcia nośne: *komunikat* / *ocena punktowa* (próg 50), *lot*, *residuum*, *pozycja*.

**Najostrzejszy rozjazd model-vs-kod.** Produkt **już ma** słownik akcji korporacyjnych — **sześć z piętnastu**
`event_type` w `src/analyzer.py:21-26` — ale w kontekście bez dostępu do lotów, i splitu nie ma
nawet tam. Po splicie lot zostaje z przedrozłamową liczbą sztuk, a jedyna dostępna naprawa
(ręczne nadpisanie) pisze tylko do pozycji, **zamieniając pozycję z lotami w pozycję bez lotów**.
Portfel nie jest gorzej napisany — jest jedyną częścią produktu, dla której nikt nie zapisał,
co musi być prawdą.

**Niezmiennik #1 i agregat.** „Stan posiadania musi dać się objaśnić operacjami" — reguła
**policzona** (`residual_holders`), **nazwana** i **logowana na produkcji**, po czym liczba wraca
do użytkownika niezmieniona. Należy do **nieistniejącego agregatu `Portfolio`/`Position`**.
Znalezisko domykające sprawę: **w całym `db/` nie ma ani jednej transakcji**
(`grep BEGIN TRANSACTION|COMMIT|ROLLBACK` → 0), a commit importu to trzy niezależne DML-e
(`src/api.py:1450-1454`) — maszyna produkująca dokładnie te naruszenia, które licznik zlicza.

**Anti-Corruption Layer.** Przecieka **`google-genai`** — jedyna zależność przekraczająca granice
warstw: **8 plików wg kryterium sukcesu** (5 produkcyjnych + 3 testowe), a w samych warstwach produkcyjnych **6 plików w 4 warstwach** — docelowo 1. Dowód absurdu: `structured_analysis` powstaje
z `json.dumps` (`src/analyzer.py:272`), więc nie może mieć trailing comma — a `src/api.py:238`
i `src/post_selection.py:38` czytają ją parserem `json5`, czyli **dwie warstwy stosują obejście
defektu Gemini do stringa, którego Gemini nigdy nie dotknął**.

## 6. Decyzje, które należą do mnie

AI było tu analitykiem: liczby pochodzą od sub-agentów i z `ast-grep`, ale żaden ranking nie
przeszedł bez zmiany. **Wybór kandydata był mój i świadomie odrzucił rekomendację
efektowniejszą** — agent stawiał na szczycie dwie rekonstrukcje stanu posiadania, ja wybrałem
domknięcie testowe adapterów, bo jako jedyne ma udokumentowaną historię kosztów: dwie awarie
produkcyjne tej samej klasy zapisane w `lessons.md`, obie złapane mechanizmem, którego wciąż nie
ma w CI. **Tezę, że dryf dokumentacji naprawi włączenie `tach` jako bramki, kupiłem od agenta
i kazałem ją sprawdzić** — exit code pokazał, że `tach check` jest dziś zielony, więc bramka
weszłaby bezkosztowo, nie chroniąc przed niczym; zostawiłem to jako świadomie niezrobione, zamiast
pozorować domknięcie. **`Unknown` domykałem pomiarem, nie domysłem**, i to się zwróciło: rozjazd
niezmiennika jest na produkcji aktywny, ale na przypadku łagodnym, więc guard z fazy 3 musi
wykluczać pozycje bez operacji — czego przed pomiarem w planie nie było. **Fazę 3 wstrzymałem do
własnej zgody**, bo tworzy i kasuje tabele w prawdziwym datasecie, a reguła projektu klasyfikuje
takie operacje jako human-only.
