---
change_id: refactor-opportunities
title: Plan — domknięcie testowe szwu odczytu + guard niezmiennika rekonstrukcji
kind: plan
created: 2026-08-08
last_updated: 2026-08-10
decided_by: Radek
tags: [architect, m4l4, plan, portfolio, guard]
---

# Plan refaktoryzacji

> **Decyzja podjęta przez człowieka, nie przez ranking.** Wybór: **C1 + tani guard z C2**.
> Ranking z `research.md` był propozycją; kolejność została zaakceptowana, ale zakres
> świadomie zawężony do jednej opcji plus jeden szybki zysk „niezależnie od rankingu".
>
> Wejście: `context/changes/refactor-opportunities/research.md` (element ④),
> `context/changes/portfolio-lots-flow/research.md` (T1–T8).

## Co refaktoryzujemy — i czego nie

**Docelowy kształt: osłona, nie zmiana struktury.** Obie wybrane pozycje mają werdykt
*świadome ograniczenie*, więc obowiązuje reguła **guard, nie przebudowa**. Żadna faza nie
zmienia kształtu kodu produkcyjnego — plan dokłada sieć bezpieczeństwa wokół dwóch decyzji,
które zostają nietknięte.

To ma konsekwencję dla wymogu „charakteryzacja przed dotknięciem": **plan nigdzie nie edytuje
niepokrytego kodu produkcyjnego**, więc charakteryzacja nie jest krokiem przygotowawczym —
jest całym produktem. Testy z faz 1–3 przybijają **obecne** zachowanie, nie zamierzone.
Świadomie utrwalamy stan zastany, łącznie z tym, co w nim niedoskonałe (problem wyroczni).

### Czego świadomie NIE robimy

| Nie robimy | Dlaczego |
| -- | -- |
| **Nie unifikujemy dwóch rekonstrukcji stanu posiadania (T1)** | Rozdział jest wymuszony, nie wybrany: FIFO nie da się wyrazić w SQL, a rekonstrukcja wprzód skasowałaby gotówkę, wpisy ręczne i spin-offy. Przypinamy tożsamość, nie usuwamy jednej ze stron |
| **Nie włączamy `tach` jako bramki (T3a)** | `tach check` przechodzi dziś na zielono; bramka nie złapałaby krawędzi, która jest powodem problemu. Wymaga granulacji modułów = osobna decyzja architektoniczna |
| **Nie ruszamy `ORDER BY` (C3)** | Wymaga najpierw **pomiaru** na produkcji (czy pary zakupów o identycznym `occurred_at` w ogóle istnieją). Dzieli plik testowy z fazą 1 — sensowne dopiero po niej. Follow-up |
| **Nie dodajemy `response_model` (T7)** | Nie występuje w `src/api.py` ani razu przy 32 trasach — to konwencja repo, nie odchył. Punktowa zmiana zwiększyłaby niespójność |
| **Nie wstrzykujemy repozytorium (P1)** | 32 endpointy i 30 plików e2e naraz. Kandydat na kampanię `/10x-test-plan`, nie na jeden plan |
| **Nie tykamy pojęć domenowych (T6, F2)** | Akcje korporacyjne i „pozycja bez lotów" → analiza DDD (M4L5) |
| **Nie dodajemy `pytest-cov`** | Kusi, bo pokrycia nie da się dziś zmierzyć — ale to zmiana narzędziowa poza zakresem decyzji i wywołałaby pełny deploy |

Wąski zakres to nie brak ambicji, tylko kontrola pola rażenia. Wszystko, co wypłynie po drodze,
dostaje follow-up, nie doklejenie do tego planu.

---

## Fazy

Kolejność: **od najtańszej i najbardziej samodzielnej**. Każda faza to **osobny, odwracalny
commit**. Faza 1 nie zależy od niczego; faza 4 zależy od 1–3.

### Faza 1 — test `list_broker_trades` *(najtańsza, w pełni samodzielna)*

Jedyne wejście produkcyjne do całego przepływu FIFO, dziś z zerowym pokryciem.

**Zmiana:** nowe funkcje testowe w `tests/test_bigquery_broker_operations.py` (tam, gdzie mieszka
właściwy idiom), przypinające obecny kształt zapytania i mapowania wyniku:
- `op_type IN ('buy', 'sell')` obecne w SQL,
- `ticker IS NOT NULL` obecne,
- `portfolio_id` w filtrze **i** w parametrach, gdy podany; nieobecne, gdy `None`,
- `ORDER BY occurred_at` obecne (pin **stanu obecnego** — nie zmieniamy go w tym planie),
- wynik to `list[dict]` z kompletem 7 kolumn, w kolejności zwróconej przez klienta.

**Uwaga wykonawcza — pułapka wykryta w researchu.** `list_broker_trades` kończy
`return [dict(row) for row in rows]` (`db/bigquery.py:4164`), więc helper `_mock_bq_client`
z `tests/test_bigquery.py` **nie zadziała** (produkuje `MagicMock`, wyłącznie dostęp
atrybutowy). Użyć `_mock_client_for_select` + `_Row(dict)`
(`tests/test_bigquery_broker_operations.py:235-250`) — „attribute and mapping access both work".

**Weryfikacja**
- *automatyczna:* `uv run pytest tests/test_bigquery_broker_operations.py` zielone; nowe testy
  **czerwienieją** po ręcznym usunięciu któregokolwiek warunku z SQL-a (kontrola, że test
  faktycznie coś trzyma, a nie tylko przechodzi).
- *ręczna:* przegląd, czy pinujemy **stan obecny**, a nie zamierzony — żaden assert nie może
  wymagać `external_id` w `ORDER BY`, bo tego dziś nie ma.

**Odwracalność:** revert jednego commita; zero wpływu na kod produkcyjny.

---

### Faza 2 — `_first_open_lot_dates` na niepustym wejściu

Dziś testowane wyłącznie przez `return_value=[]` (`tests/test_bigquery.py:1770-1778`), więc cały
adapter biegnie na pustej liście i żadna arytmetyka lotów nie jest weryfikowana — mimo nazwy
testu sugerującej pokrycie daty nabycia.

**Zmiana:** test w `tests/test_bigquery.py` podający **niepuste** operacje i sprawdzający, że
`first_buy_date` faktycznie trafia do wiersza pozycji, z poprawnym kluczem `(portfolio_id, ticker)`.
Przypadek graniczny do przybicia: ticker **bez otwartych lotów** ma klucz **nieobecny**, nie
wyzerowany (`src/portfolio_lots.py:350-360`).

**Weryfikacja**
- *automatyczna:* nowy test czerwienieje po podmianie klucza krotki na `row["portfolio_id"]`
  zamiast `portfolio_id or ""` — to jest dokładnie ten błąd, który dziś przeszedłby niezauważony.
- *ręczna:* sprawdzić, że test nie duplikuje `tests/test_portfolio_lots.py` — ma pokrywać
  **adapter**, nie czystą funkcję.

**Odwracalność:** osobny commit, revert bez skutków ubocznych.

---

### Faza 3 — guard niezmiennika rekonstrukcji *(tani zysk z C2)*

Przypięcie tożsamości, na której stoi cała architektura historii wartości, a która została
zmierzona **raz** (2026-08-05, 508 operacji produkcyjnych) i nigdy nie zakodowana:

```
ledger_open(D) + residual  ==  shares_on_day(D)
```

**Zmiana:** nowa kontrola `_check_reconstruction_agreement()` w
`scripts/test_bq_portfolio_time_dimension.py`, dopisana do listy wołań w `main()` (dziś dziewięć
`_check_*`, `:499-507`). Dla **tego samego zasianego zestawu zdarzeń** porównuje liczbę sztuk
z CTE `ops_daily`/`ops_totals` z wartością wyprowadzoną z `basis_segments`/`first_open_lot_dates`.

**Nie tyka żadnej z dwóch implementacji.** Skrypt korzysta z istniejącej maszynerii: tabele
jednorazowe `_rt_<uuid>`, monkeypatch stałych z nazwami tabel, sprzątanie w `finally`
(`:511-513`).

**Doprecyzowanie po pomiarze produkcyjnym (2026-08-10).** Pomiar P1
(`portfolio-lots-flow/research.md`, sekcja „Pomiary produkcyjne") pokazał, że na produkcji
**jeden ze 108 wierszy `holders` już dziś łamie tę tożsamość** — pozycja `XTB` wpisana ręcznie,
bez ani jednej operacji w swoim portfelu. To przypadek **z definicji poprawny**, świadomy
fallback opisany w komentarzu `db/bigquery.py:975-979`. Konsekwencja dla tej fazy jest twarda:
kontrola musi porównywać tożsamość **z wyłączeniem walorów, dla których zbiór operacji jest
pusty**, inaczej poczerwienieje na starcie na danych, które są w porządku — a guard, który
czerwieni się bez powodu, zostanie wyłączony w tydzień. Zasiany zestaw zdarzeń w skrypcie
**musi zawierać oba przypadki**: walor z operacjami (tożsamość ma trzymać) i pozycję bez
operacji (kontrola ma ją pominąć, a nie zaliczyć jako naruszenie).

To jest też odpowiedź na pytanie, którego wcześniej nie umiałem zadać: `residual_holders` jest
dziś **logowane, ale nie ma wartości bazowej** — nikt nie zapisał, że „1" to normalny stan tego
systemu. Alarm bez linii bazowej nie jest alarmem.

**Dlaczego nie zwykły pytest — i to jest ograniczenie, nie wybór.** Strona SQL wykonuje się
w BigQuery; zamockowana porównywałaby mock ze sobą. `tests.yml` nie ma uwierzytelnienia do GCP.
Odpowiedź na „test czy żywe dane" brzmi: **ani jedno — zasiane dane, prawdziwy silnik**.

**Weryfikacja**
- *automatyczna:* **`tests/test_roundtrip_script_wiring.py::test_every_check_function_is_called_from_main`**
  — parsuje skrypt `ast`-em i sprawdza, że każda funkcja `_check_*` jest wołana z `main()`.
  Nie uruchamia BigQuery, więc chodzi w bramce `Tests` bez poświadczeń. Łapie dokładnie ten
  sposób, w jaki ta faza może zawieść po cichu: kontrola **napisana, ale niepodpięta** — skrypt
  wypisze wtedy „All checks passed", a właściwość nie będzie sprawdzona. Test istnieje i jest
  zielony **dziś** (9 zdefiniowanych, 9 wołanych), więc zaczerwieni się w momencie dodania
  `_check_reconstruction_agreement()` bez wpisu w `main()`.
  Czego **nie** obejmuje: samej równoważności rekonstrukcji — do tego trzeba prawdziwego silnika.
- *ręczna:* uruchomić skrypt; kontrola musi przejść na obecnym kodzie. Następnie **celowo
  zepsuć jedną stronę** (np. zmienić znak w `signed_volume`) i potwierdzić, że kontrola
  czerwienieje. Guard, którego nie widziano na czerwono, nie jest guardem.

**Prerekwizyt — wymaga Twojej zgody, nie mojej.** Skrypt tworzy i kasuje tabele w prawdziwym
datasecie (`client.delete_table(rt_ref, not_found_ok=True)`). Kasuje wyłącznie to, co sam
stworzył, ale `CLAUDE.md` klasyfikuje destrukcyjne operacje na BigQuery jako **human-only** —
więc rozszerzenie i uruchomienie potwierdzasz świadomie, zamiast zakładać, że się mieści.

**Odwracalność:** osobny commit dodający jedną funkcję i jedną linię wywołania.

---

### Faza 4 — egzekwowanie, włączane osobno

**Mechanizm ląduje na zielono w fazach 1–3; dopiero ta faza cokolwiek egzekwuje.** To jawnie
rozdzielone, żeby żaden z poprzednich commitów nie mógł zablokować merge'a przez własną
niedojrzałość.

**Zmiana:** udokumentowanie w `context/foundation/lessons.md`, że kontrola z fazy 3 jest
**obowiązkowym krokiem ręcznym** przed zmianą którejkolwiek z dwóch rekonstrukcji — dokładnie
tak, jak `lessons.md:211-235` (PUL-29) zapisało round-trip jako obowiązkowy po awarii z `400
Syntax error`.

**Czego ta faza świadomie NIE robi:** nie wstawia skryptów `scripts/test_bq_*.py` do CI. Żaden
z 14 istniejących tam nie biegnie w workflow, wymagają poświadczeń GCP i tworzą tabele.
Wciągnięcie ich do CI to osobna decyzja infrastrukturalna z własnym kosztem — **follow-up,
nie doklejenie tutaj**.

**Weryfikacja**
- *automatyczna:* **`tests/test_roundtrip_script_wiring.py::test_scripts_cited_in_lessons_still_exist`**
  — sprzęga `lessons.md` ze skryptami, na które się powołuje: każda wymieniona ścieżka
  `scripts/*.py` musi istnieć. To jest właśnie **moment, w którym reguła przestaje być
  konwencją**: zapis w `lessons.md` staje się egzekwowalny, bo skasowanie mechanizmu, na który
  wskazuje, zaczerwieni bramkę `Tests`. Ten sam wzorzec, co
  `tests/test_deploy_workflow_filter.py` sprzęgający `deploy.yml` z `.dockerignore` — gdy dwa
  artefakty muszą się zgadzać, komentarz nie wystarcza.
  Czego **nie** obejmuje: czy treść zapisu jest sensowna. Test pilnuje istnienia celu, nie jakości reguły.
- *ręczna:* czy zapis mówi, **kiedy** kontrolę uruchomić (przed zmianą `ops_daily`/`ops_totals`
  albo `basis_segments`), a nie tylko, że istnieje.

**Odwracalność:** zmiana wyłącznie w `context/**` → **nie wywołuje deployu**
(`deploy.yml:16-18`, `paths-ignore`).

---

## Koszt uboczny, o którym trzeba wiedzieć

Fazy 1–3 dotykają `tests/` i `scripts/`, więc **każda z nich — łącznie z rewertem — wywoła
pełny deploy** (build obrazu + 5 `gcloud run jobs update` + `run deploy`). Odwracalne, ale nie
darmowe. Jedyna faza darmowa to 4. Warto rozważyć złożenie faz 1–2 w jeden PR, skoro dzielą
obszar i obie są czysto testowe.

**~~Otwarte ryzyko~~ ZAMKNIĘTE 2026-08-10.** Nie było wiadomo, jak bramka
`ai-code-review/verdict` (`pass AND min ≥ 4` na 6 kryteriach) ocenia PR **czysto testowy**,
bez zmian w kodzie produkcyjnym — dotyczyło wszystkich faz. Zmierzone przy okazji, na przypadku
ostrzejszym: PR #260 miał diff **wyłącznie markdownowy** i dostał `pass` z **min score 10**
(10/10 w każdym z sześciu kryteriów, łącznie z `testCoverageVsRisk`). Scorer nie karze braku
testów w PR, który z natury ich nie ma. Ryzyko zdjęte ze wszystkich faz; `ai-cr:override`
nie będzie potrzebne. **Jeden pomiar, nie rozkład** — mówi, że bramka to potrafi, nie że
zrobi tak zawsze.

---

## Follow-upy (nie w tym planie)

1. **C3** — pomiar par zakupów o identycznym `occurred_at`, potem `ORDER BY occurred_at,
   external_id`.
2. **T6** — asymetria gwarancji „monotonicznie samo-naprawiające": obowiązuje dla rekonstrukcji
   SQL, **nie** dla ledgera FIFO. Archiwum tego nie odnotowuje.
3. **P1** — wstrzyknięcie repozytorium jako kampania.
4. **`context/foundation/test-plan.md`** — status `draft` z 2026-06-08, deklaruje „No integration
   or end-to-end tests exist yet", co jest dziś nieprawdą.
5. **Komentarz `KNOWN VIOLATION` w `tach.toml`** — wymienia 3 z 4 krawędzi. Kosmetyka, ale
   w projekcie o bus factor 1 dokumentacja jest jedyną pamięcią.

## Przekazanie do implementacji

```
/10x-plan-review refactor-opportunities     # świeża sesja, przed implementacją
/10x-implement refactor-opportunities phase 1
```

**Dla ścieżki 10xArchitect implementacja nie jest wymagana** — M4L4 kończy się na planie.
