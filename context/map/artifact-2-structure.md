# Artefakt 2 — Mapa strukturalna (graf zależności)

> Deep Focus, `puls-gpw`. Zebrane 2026-08-07, wychodząc od
> `context/map/artifact-1-territory.md`.

## 0. Uwaga o narzędziu

Prompt lekcji zakłada `dependency-cruiser` — narzędzie dla ekosystemu JS/TS.
**`puls-gpw` to projekt w Pythonie**, więc `dependency-cruiser` nie ma tu czego analizować
(jedyny front to statyczny `index.html` bez modułów ES). Zastosowano odpowiedniki:

| Rola z lekcji | Narzędzie użyte tutaj | Uzasadnienie |
| -- | -- | -- |
| konfiguracja granic warstw | **`tach` 0.35.0** (`tach.toml`) | **już był w projekcie** — granice modułów są zadeklarowane i sprawdzane |
| graf importów, fan-in/fan-out, cykle | własny przebieg po AST (`ast.parse`) | brak zależności zewnętrznej, pełna kontrola nad filtrowaniem |
| raport zewnętrznych zależności | `tach check-external` | patrz zastrzeżenie w §5 |

Frontend nie ma grafu do zbadania z powodu, który sam jest wnioskiem: **cały SPA to jeden plik
6326 linii** (`static/index.html`), bez podziału na moduły. Nie ma krawędzi, bo nie ma węzłów.

## 1. Pięć najważniejszych obserwacji

1. **Warstwa danych importuje w górę, do warstwy logiki — i jest to świadomie zadeklarowane.**
   `db/bigquery.py` importuje z `src/` w czterech miejscach. `tach check` przechodzi na zielono
   **nie dlatego, że naruszenia nie ma, tylko dlatego, że zostało wpisane do `tach.toml` jako
   dozwolone**, z komentarzem `# SMELL`.
2. **Komentarz dokumentujący to naruszenie jest niekompletny.** Wymienia trzy importy, a w kodzie
   są cztery. `src.portfolio_lots` nie jest nigdzie odnotowany. To dryf dokumentacji od kodu —
   dokładnie ta klasa błędu, przed którą chroni artefakt strukturalny.
3. **Brak cykli importu.** Ani jednego dwukierunkowego sprzężenia między modułami. Przy
   4493-liniowym `bigquery.py` to lepszy wynik, niż sugerowałby rozmiar.
4. **`src/exceptions.py` jest prawdziwym fundamentem** (fan-in 11), a nie `db` ani `src.api`.
   Graf ma zdrowy kształt „wąskiego dołu": najczęściej importowane są małe, bezstanowe moduły.
5. **Ryzyko testowalności jest skoncentrowane w jednym miejscu i mierzalne.**
   `src/api.py` ściąga **51 nazw** z `db.bigquery`, a `tests/e2e/conftest.py` musi łatać
   **62 symbole**, żeby testy e2e w ogóle wystartowały.

## 2. Granice warstw

Zadeklarowana architektura (`tach.toml`): trzy punkty wejścia (`main`, `post_main`, `api_main`)
→ `src` → `db`. `db` nie powinien mieć zależności w górę.

| Sprawdzana granica | Wynik | Dowód | Dlaczego to ważne przy zmianie | Związek z artefaktem 1 | Co sprawdzić dalej |
| -- | -- | -- | -- | -- | -- |
| `db` → brak zależności w górę | ⚠️ **NARUSZONA, dozwolona wpisem** | `tach.toml:42-46` deklaruje `db depends_on src`; graf AST potwierdza 4 krawędzie | dopóki `db` zna `src`, nie da się wyciąć warstwy danych ani przetestować jej bez logiki | `db/bigquery.py` = 71 dotknięć, #2 w rankingu | czy 4 importy da się przenieść do `db/exceptions.py` + wspólnego liścia |
| komentarz w `tach.toml` = stan faktyczny | ❌ **ROZJECHANY** | komentarz wymienia `exceptions`, `post_selection`, `tickers`; kod ma też `portfolio_lots` (`db/bigquery.py:43`) | ktoś planujący naprawę wyceni ją na 3 przeniesienia zamiast 4 | — | uzupełnić komentarz albo zamienić go na test |
| punkty wejścia → tylko `src` i `db` | ✅ OK | `tach check`: `All modules validated!` | siedem punktów wejścia nie miesza się ze sobą | `.github/workflows` 30 dotknięć — każdy entry point ma swój job | — |
| brak cykli | ✅ OK | przebieg AST: `brak cykli bezposrednich` | zmiana w dowolnym module ma przewidywalny kierunek propagacji | — | — |

### Cztery krawędzie naruszające warstwę

```
db.bigquery -> src.exceptions      (BigQueryError)
db.bigquery -> src.portfolio_lots  ← NIEUDOKUMENTOWANA
db.bigquery -> src.post_selection  (select_top_companies)
db.bigquery -> src.tickers         (is_valid_ticker)
```

Łagodząca okoliczność, i to prawdziwa: **oba nieudokumentowane i udokumentowane cele są
liśćmi**. `src/tickers.py` i `src/portfolio_lots.py` nie importują niczego poza biblioteką
standardową. Naruszenie jest więc „płaskie" — nie wciąga za sobą łańcucha zależności.
`tach.toml` uzasadnia to przy `tickers` decyzją z PUL-102: strażnik kształtu tickera musi stać
**na granicy zapisu**, inaczej zmiana markupu u źródła go ominie. To jest obrona projektowa,
nie wymówka — ale dotyczy jednego z czterech importów, nie wszystkich.

## 3. Kształt grafu — fan-in i fan-out

**Fan-in (ile modułów mnie importuje) — fundament systemu:**

| Moduł | Fan-in | Rozmiar | Charakter |
| -- | -- | -- | -- |
| `src.exceptions` | **11** | mały | prawdziwy fundament — wspólny słownik błędów |
| `db.bigquery` | 9 | **4493** | warstwa danych |
| `src.notifier` | 7 | 561 | powiadomienia |
| `src.http_client` | 7 | mały | wspólny klient HTTP (to on obchodzi blokadę gpw.pl) |
| `src.logging_setup` | 6 | mały | |
| `src.tickers` | 4 | mały | |

**Fan-out (ile modułów importuję) — orkiestratorzy:**

| Moduł | Fan-out | Uwaga |
| -- | -- | -- |
| `post_main` | 8 | punkt wejścia |
| **`src.api`** | **7** | jedyny nie-entry-point w czołówce |
| `company_stats_main`, `main` | 6 | punkty wejścia |
| `cost_report_main`, `etf_quotes_main`, `_run_generate_post` | 4 | punkty wejścia |
| `db.bigquery` | 4 | = cztery naruszenia warstwy z §2 |

Czołówkę fan-outu zajmują punkty wejścia — i tak być powinno. **Jedynym wyjątkiem jest
`src/api.py`**, który zachowuje się jak ósmy punkt wejścia: 1568 linii, 32 endpointy, fan-out 7.
To nie jest moduł logiki, to warstwa kompozycji udająca moduł.

## 4. Ryzyka testowalności

### Podsumowanie

Ryzyko nie jest rozproszone. Siedzi w jednym połączeniu: **`src/api.py` ↔ `db/bigquery.py`**.

`src/api.py` importuje **51 nazw** z `db.bigquery` dwiema instrukcjami `from db.bigquery import
(...)`. Ponieważ importy są wciągane do przestrzeni nazw `src.api`, test nie może podmienić
warstwy danych w jednym miejscu — musi załatać **każdą nazwę osobno, pod ścieżką `src.api.X`**.

Efekt widać w liczbach: `tests/e2e/conftest.py` ma **910 linii i 62 łatane symbole**, w tym
`patch("src.api.list_announcements_admin", …)`, `patch("src.api.get_latest_snapshot_for_wallet", …)`
i sześćdziesiąt podobnych.

### Lista ryzyk testowych

| Ryzyko | Dowód | Konsekwencja |
| -- | -- | -- |
| **Każdy nowy endpoint oparty o BigQuery wymaga dopisania mocka w `conftest.py`** | 62 łatki, `conftest.py` #3 w rankingu aktywności (50 dotknięć) | pominięcie → test e2e wywala się na starcie aplikacji, nie na asercji; komunikat myli |
| `conftest.py` rośnie liniowo z liczbą funkcji, nie z liczbą testów | 910 linii wspólnego setupu na 30 plików testowych | koszt utrzymania testów rośnie razem z produktem, mimo że testy się nie zmieniają |
| `db/bigquery.py` jest nietestowalny w izolacji | 106 funkcji, importuje w górę do `src` | testy warstwy danych ciągną logikę domenową |
| Frontend nie ma jednostki do testowania | `index.html` 6326 linii, brak modułów | **cała weryfikacja UI musi iść przez e2e** — stąd 208 dotknięć `tests/e2e` |

### Najbardziej podejrzane moduły

1. **`src/api.py`** — 51 importów z warstwy danych, 32 endpointy, fan-out 7. Punkt, w którym
   spotyka się wszystko.
2. **`db/bigquery.py`** — 106 funkcji w jednym pliku, cztery importy w górę. Kandydat na
   rozbicie po domenach (`portfolio`, `announcements`, `stats`, `x_posts`).
3. **`tests/e2e/conftest.py`** — nie jest problemem sam w sobie, jest **miernikiem** problemu
   nr 1. Jeśli spadnie, znaczy że naprawa zadziałała.

### Co sprawdzić dalej

- Czy 51 importów w `src/api.py` da się zastąpić wstrzykiwaniem jednego obiektu repozytorium —
  wtedy `conftest.py` łata **jeden** symbol zamiast 62.
- Czy `db/bigquery.py` dzieli się na sekcje domenowe, czy funkcje są przemieszane
  (to zadanie artefaktu domenowego, M4L5).
- Ile z 30 plików e2e realnie potrzebuje pełnego mocka, a ile mogłoby działać na węższym.

## 5. Zastrzeżenie do `tach check-external`

`tach check-external` zgłasza 7 „nieużywanych" pakietów zewnętrznych (`google_cloud_bigquery`,
`python_dotenv`, `pyjwt`, `google_genai`, …) i niezadeklarowane zależności `dotenv`.
**To fałszywe alarmy narzędzia, nie defekty projektu** — wynikają z rozjazdu między nazwą
dystrybucji na PyPI a nazwą modułu importu (`python-dotenv` → `import dotenv`,
`google-cloud-bigquery` → `import google.cloud.bigquery`). Sprawdzone ręcznie: wszystkie siedem
pakietów jest realnie używanych. Nie wyciągam z tego wniosków i nie zaliczam do długu.

## 6. `unknowns`

- **Graf pokazuje importy, nie wywołania.** Że `src.api` importuje 51 nazw, nie znaczy, że
  używa wszystkich na każdej ścieżce.
- **Frontend jest poza grafem.** 6326 linii `index.html` to biała plama — jedyne, co o nim
  wiadomo strukturalnie, to że nie ma struktury.
- **Nie mierzono kosztu środowiska uruchomieniowego.** Czy 51 importów spowalnia zimny start
  Cloud Run — nie sprawdzano.
- **`oldProjectData/`** jest wykluczony z `tach.toml` (`exclude`), więc graf nic o nim nie mówi.
  Artefakt 1 wskazał go jako kandydata do usunięcia; to nadal niepotwierdzone.
