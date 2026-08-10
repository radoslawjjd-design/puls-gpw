# Artefakt 1 — Mapa terytorium (dowody z historii gita)

> Wide Scan, `puls-gpw`. Zebrane 2026-08-07 z historii gita.
> Metoda: `git log --since=… --name-only`, z odfiltrowaniem szumu (`uv.lock`, `*.lock`,
> `package-lock.json`). Liczby to **dotknięcia pliku w commitach**, nie linie kodu.

## 0. Zakres — ważne zastrzeżenie

Prompt zakłada okno 12 miesięcy. **Repozytorium jest młodsze niż to okno:**

| | |
| -- | -- |
| pierwszy commit | 2026-05-20 |
| ostatni commit | 2026-08-07 |
| długość historii | **79 dni** |
| liczba commitów | **499** |

„Ostatnie 12 miesięcy" = całe życie projektu. Z tego samego powodu **podział na kwartały nie
niesie informacji** — zamiast niego użyto podziału miesięcznego (§2). Wszystkie wnioski o
„trendzie" dotyczą jedenastu tygodni, nie roku; to za mało, żeby mówić o obszarach trwale
zamrożonych — mówimy o obszarach *jeszcze nietkniętych*.

## 1. Aktywność — gdzie projekt był realnie dotykany

### a) Obszary

| # | Obszar | Dotknięcia | Uwaga |
| -- | -- | -- | -- |
| 1 | `context/changes` | 1073 | dokumentacja procesu, nie kod — patrz niżej |
| 2 | `tests/e2e` | 208 | |
| 3 | `(root)` | 115 | `CLAUDE.md`, `AGENTS.md`, `pyproject.toml`, `Dockerfile` |
| 4 | `static/index.html` | 95 | **pojedynczy plik**, nie katalog |
| 5 | `oldProjectData/agents` | 78 | martwy balast — patrz §5 |
| 6 | `db/bigquery.py` | 71 | **pojedynczy plik** |
| 7 | `.claude/skills` | 58 | |
| 8 | `src/api.py` | 48 | **pojedynczy plik** |
| 9 | `context/foundation` | 41 | |
| 10 | `.github/workflows` | 30 | |

**Pierwszy wniosek jest o procesie, nie o kodzie.** Najgorętszym obszarem repozytorium
z ogromną przewagą jest `context/changes` — dokumentacja zmian (`change.md`, `research.md`,
`plan.md`, `impl-review.md`) wytwarzana przez workflow 10x. To nie jest szum do odfiltrowania:
to najmocniejszy dowód, że proces projektowy istnieje i został utrzymany przez wszystkie 499
commitów. 79 zamkniętych zmian leży w `context/archive/`.

Trzy z dziesięciu pozycji rankingu to **pojedyncze pliki**, nie katalogi — po zejściu poziom
niżej okazało się, że nie ma sensownego katalogu do wskazania, bo cała odpowiedzialność siedzi
w jednym pliku. To główna obserwacja strukturalna tej mapy (§4).

### b) Pliki kodu (z pominięciem `context/` i dokumentacji)

| # | Plik | Dotknięcia | Rozmiar dziś |
| -- | -- | -- | -- |
| 1 | `static/index.html` | 95 | **6326 linii** |
| 2 | `db/bigquery.py` | 71 | **4493 linie** |
| 3 | `tests/e2e/conftest.py` | 50 | 910 linii |
| 4 | `src/api.py` | 48 | 1568 linii |
| 5 | `tests/test_api.py` | 47 | |
| 6 | `tests/test_bigquery.py` | 45 | |
| 7 | `.github/workflows/deploy.yml` | 21 | |
| 8 | `src/post_generator.py` | 20 | 490 linii |
| 9 | `src/analyzer.py` | 15 | 299 linii |
| 10 | `src/notifier.py` | 13 | 561 linii |

## 2. Rozkład w czasie (miesięcznie)

| Miesiąc | Czym żył projekt | Szczyt |
| -- | -- | -- |
| **2026-05** | bootstrap — jeden commit dotykający kodu (`deploy.yml`) | — |
| **2026-06** | budowa MVP; UI i warstwa danych rosną równolegle | `index.html` 56, `bigquery.py` 40, `test_bigquery.py` 26 |
| **2026-07** | funkcje użytkownika (auth, portfel, powiadomienia); **wchodzą testy e2e** | `index.html` 22, `e2e/conftest.py` 20, `bigquery.py` 19 |
| **2026-08** | utrzymanie i praca infrastrukturalna; tempo spada, e2e zostaje | `e2e/conftest.py` 9, `index.html` 9, `bigquery.py` 9 |

Nacisk przesunął się z *budowania* (czerwiec) przez *funkcje* (lipiec) do *utrzymania*
(sierpień). Charakterystyczne jest wejście `tests/e2e/conftest.py` do ścisłej czołówki w lipcu
i utrzymanie się tam do dziś — to nie jest plik, który powinien zmieniać się co tydzień
w dojrzałym projekcie (§4).

## 3. Współzmiany — co zmienia się razem

| # | Zestaw katalogów | Wspólne commity |
| -- | -- | -- |
| 1 | `src` + `tests` | 34 |
| 2 | `static` (sam) | 32 |
| 3 | `static` + `tests/e2e` | 28 |
| 4 | `src` (sam) | 28 |
| 5 | **`db` + `src` + `static` + `tests` + `tests/e2e`** | **17** |
| 6 | `.github/workflows` | 14 |
| 7 | `db` + `tests` | 12 |

### Wnioski dla top 3

1. **`src` + `tests` (34)** — zdrowe sprzężenie. Zmiana logiki idzie z testem w tym samym
   commicie. To dyscyplina, nie dług.
2. **`static` sam (32)** — zmiany czysto frontendowe nie pociągają niczego innego. Dobrze:
   UI jest odseparowany od backendu kontraktem HTTP.
3. **`static` + `tests/e2e` (28)** — każda istotna zmiana UI ciągnie za sobą test e2e.
   Też zdrowe, ale w połączeniu z pozycją 2 pokazuje, że `index.html` jest zarazem
   samodzielny i wszechobecny.

**Pozycja 5 jest ostrzeżeniem.** 17 commitów dotyka jednocześnie pięciu warstw — bazy, logiki,
UI, testów jednostkowych i e2e. To sygnatura „pionowego plastra": dodanie jednej funkcji
wymaga dotknięcia całego stosu. Dla MVP to naturalne, ale przy 4493-liniowym `bigquery.py`
oznacza, że koszt dołożenia funkcji rośnie z każdą kolejną.

## 4. Wspólny mianownik — pliki spinające całe repo

Trzy pliki pełnią rolę „wspólnego mianownika" i to one, a nie żaden katalog, wyznaczają
realną architekturę projektu:

| Plik | Linie | Rola | Dlaczego to boli |
| -- | -- | -- | -- |
| `static/index.html` | **6326** | **cały frontend SPA w jednym pliku** — markup, style i logika razem | każda zmiana UI ląduje w tym samym pliku; brak granic modułowych, konflikty i regresje trafiają w jedno miejsce |
| `db/bigquery.py` | **4493** | cały dostęp do danych — DDL, zapytania, MERGE-e, schematy dla wszystkich domen | zmiana w portfelu i zmiana w komunikatach modyfikują ten sam plik; to on wymusza współzmianę z pozycji 5 |
| `tests/e2e/conftest.py` | 910 | wspólne mockowanie BigQuery dla wszystkich testów e2e | **każdy nowy endpoint oparty o BQ wymaga dopisania mocka tutaj** — plik rośnie liniowo z liczbą funkcji i jest stałym punktem tarcia |

Rozkład w `src/` potwierdza ten obraz — praca jest skrajnie skoncentrowana:

```
72  db/bigquery.py        ← 1 plik
48  src/api.py            ← 1 plik
20  src/post_generator.py
15  src/analyzer.py
13  src/notifier.py
—— poniżej: 25 modułów z ≤6 dotknięciami, w tym 11 z jednym ——
```

Dwa pliki (`db/bigquery.py`, `src/api.py`) zbierają **120 ze wszystkich dotknięć `src/` i `db/`**,
podczas gdy 25 pozostałych modułów razem zbiera mniej. Domenowe moduły
(`portfolio_treemap`, `portfolio_calendar`, `gpw_archive`, `cost_report`, …) są małe, świeże
i stabilne — dług nie jest rozproszony, jest skupiony w dwóch miejscach.

## 5. Obszary nietknięte i martwe

- **`oldProjectData/`** — 11 plików, 78 dotknięć skoncentrowanych na starcie projektu.
  Katalog jest nadal w drzewie roboczym, ale nie jest importowany przez kod produkcyjny.
  Kandydat do usunięcia; do potwierdzenia w artefakcie strukturalnym.
- **11 modułów w `src/` z jednym dotknięciem** (`tickers.py`, `polish_numbers.py`,
  `gpw_etf_metrics.py`, `gpw_archive.py`, `cost_report.py`, `post_selection.py`,
  `portfolio_thread_composer.py`, …) — napisane raz i niezmieniane. Część to świeże moduły
  (`cost_report.py` z 2026-08-07), część to stabilne narzędzia. **Nie należy tego mylić
  z martwym kodem** — przy 79-dniowej historii brak zmian nie dowodzi braku użycia.

## 6. Weryfikacja — czy gorące pliki nadal istnieją

Sprawdzono, bo historia mogła wskazywać na pliki usunięte lub przeniesione. Wszystkie
pozycje z rankingu istnieją w bieżącym drzewie:

```
OK  static/index.html      6326 linii
OK  db/bigquery.py         4493 linie
OK  src/api.py             1568 linii
OK  tests/e2e/conftest.py   910 linii
OK  src/post_generator.py   490 linii
OK  src/notifier.py         561 linii
OK  src/analyzer.py         299 linii
```

Żaden wniosek tej mapy nie opiera się na pliku, którego już nie ma.

## 7. `unknowns` — czego ta mapa NIE mówi

- **Nie mówi, co jest używane.** Historia gita mierzy zmiany, nie ruch. Moduł nietknięty od
  czerwca może obsługiwać każde żądanie.
- **Nie ma sygnału o autorstwie.** Repozytorium ma jednego autora (`Radek`), więc artefakt
  kontrybutorski (`m4l2-3-contributors-git`) nie odpowie na pytanie „kto co wie" — odpowie
  „jedna osoba wie wszystko", co samo w sobie jest ryzykiem projektowym, ale nie mapą wiedzy.
- **Nie widzi struktury wewnątrz plików.** Że `bigquery.py` ma 4493 linie, wiadomo; czy
  dzieli się na czytelne sekcje domenowe — nie. To zadanie artefaktu strukturalnego.
- **79 dni to krótkie okno.** Rozróżnienie „gorące vs zamrożone" jest tu słabe; mocne jest
  tylko rozróżnienie „skoncentrowane vs rozproszone".
