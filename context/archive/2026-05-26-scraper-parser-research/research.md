# F-01 Research: Bankier.pl HTML & ESPI/EBI PDF Format

> Wygenerowane: 2026-05-27. Zamyka OQ-2 i dostarcza kontekst dla S-01 i S-02.

---

## HTML Structure (Bankier.pl)

**URL listingu:** `https://www.bankier.pl/gielda/wiadomosci/komunikaty-spolek`

### Aktywny selektor listy

| Selektor | Wynik (Phase 1) | Uwaga |
|---|---|---|
| `.m-quotes-announcements-item` | **25 elementów — AKTYWNY** | Primary selector — używaj tego |
| `.listingItem` | 0 elementów | Nieaktywny |
| `li[class*='item']` | 377 elementów | Zbyt ogólny (dopasowuje nav, sidebar itp.) |

### Selektory per item

| Pole | Selektor | Format / Uwagi |
|---|---|---|
| **Link + tytuł** | `.m-quotes-announcements-item__anchor` | `<a href="https://www.bankier.pl/wiadomosc/...">` — jeden element, łączy tytuł z URL |
| **Data** | `.m-quotes-announcements-item__date` | Format: `DD.MM.YYYY HH:MM` — 25/25 itemów ma datę |
| **Źródło (ESPI/EBI)** | `.a-quotes-badge .value` | Tekst: `"espi"` lub `"ebi"` |
| **Company** | ❌ brak w liście | Nie istnieje jako osobny element w widoku listingu |
| **Ticker giełdowy** | ❌ brak w liście | Nie istnieje jako osobny element w widoku listingu |

> **Zmiana vs `oldProjectData/bankier.py`:** Stary selektor linku `a[href]` w item zastąpiony przez
> `.m-quotes-announcements-item__anchor`. Stary selektor tickera `a[href*="profile/quote.html"]`
> w liście **nie istnieje** — ticker wymaga 2-hop (patrz niżej).

### Ticker i nazwa spółki — ścieżka 2-hop

Ticker i pełna nazwa spółki są **niedostępne na liście** — wymagają dwóch dodatkowych requestów:

```
1. Strona ogłoszenia (bankier.pl/wiadomosc/...)
   └── div.m-quote-list__row.-stock
       └── div.l-quote-group
           └── a[href*="profile/quote.html?symbol=SFKPOLKAP"]
               └── URL zawiera symbol ESPI (np. SFKPOLKAP)

2. Strona profilu spółki (bankier.pl/inwestowanie/profile/quote.html?symbol=SFKPOLKAP)
   └── span.a-heading__suffix.-blue.-with-dot
       └── Tekst: "Polkap SA w restrukturyzacji (SFK)"
           └── ticker giełdowy = zawartość nawiasów: "SFK"
```

> **Uwaga:** Symbol w URL profilu (`SFKPOLKAP`) ≠ ticker giełdowy (`SFK`).
> Ticker giełdowy to tekst w nawiasach w `span.a-heading__suffix`.

### Paginacja

Lista Bankier.pl używa prostej paginacji URL:
- Strona 1: `/komunikaty-spolek` lub `/komunikaty-spolek/1` (równoważne)
- Strona N: `/komunikaty-spolek/N`

Każda strona zwraca 25 elementów, posortowanych od najnowszego.

**Stop condition dla scrapera (okno 15 min):**
```
page = 1
loop:
    pobierz /komunikaty-spolek/{page}
    zbierz itemy gdzie date >= now - 15min
    jeśli ostatni item na stronie (najstarszy) >= now - 15min:
        page += 1   ← są jeszcze nowe ogłoszenia na następnej stronie
    else:
        STOP        ← wyszliśmy poza okno 15 min
```

W normalnym ruchu strona 1 pokrywa ~1.5h ogłoszeń — paginacja będzie potrzebna
rzadko (np. sezon wyników, wiele raportów jednocześnie).

### Dostępne metadane per item (z listingu)

| Pole | Źródło | Typ |
|---|---|---|
| `title` | `.m-quotes-announcements-item__anchor` getText | `str` |
| `bankier_url` | `.m-quotes-announcements-item__anchor` href | `str` |
| `date` | `.m-quotes-announcements-item__date` getText | `datetime` (parse `DD.MM.YYYY HH:MM`) |
| `source` | `.a-quotes-badge .value` getText | `"espi"` \| `"ebi"` |
| `espi_code` | prefix tytułu przed `:` (np. `SFKPOLKAP`) | `str` — kod raportowy, nie ticker giełdowy |

Pola wymagające 2-hop (opcjonalne w MVP):

| Pole | Wymaga | Typ |
|---|---|---|
| `ticker` | fetch strony ogłoszenia + profilu | `str` (np. `SFK`) |
| `company` | fetch strony profilu | `str` (np. `Polkap SA w restrukturyzacji`) |

---

## PDF Format Analysis

Próbka: 25 ogłoszeń z listingu (2026-05-27), ręczna weryfikacja właściciela produktu.

> **Korekta vs wyniki `pdf_sampler.py`:** Skrypt błędnie klasyfikował jako `NO_PDF` ogłoszenia,
> które mają treść w `table.seauid2 tbody`. Poniższa tabela zawiera poprawioną klasyfikację.

| # | Ticker | Treść główna | PDFy | Uwagi |
|---|---|---|---|---|
| 1 | TORPOL | `table.seauid2` | — | |
| 2 | INSIDPARK | `table.seauid2` | 1 PDF | |
| 3 | BGZBNPP | `table.seauid2` | 1 PDF (RB31_BNPP_...) | |
| 4 | PLOTTWIST | — | **4 PDFy** | EBI raport roczny — brak seauid2 |
| 5 | ERSTEPL | `table.seauid2` | 1 PDF (Zawiadomienie_...) | |
| 6 | ERSTEPL | `table.seauid2` | 1 PDF (Zawiadomienie_...) | |
| 7 | BUDIMEX | `table.seauid2` | — | |
| 8 | SFKPOLKAP | `section.o-article-content` 2. br | — | 1. br = AI summary Bankier |
| 9 | KETY | `table.seauid2` | — | |
| 10 | PROGUNSGR | `table.seauid2` | **4 PDFy** | |
| 11 | JRCGROUP | `table.seauid2` | 1 PDF | |
| 12 | ATOMJELLY | `table.seauid2` | — | |
| 13 | NOVITA | `table.seauid2` | **5 PDFów** | |
| 14 | PROGUNSGR | `table.seauid2` | **3 PDFy** | |
| 15 | MERA | — | **4 PDFy** | EBI raport roczny — brak seauid2 |
| 16 | CZARNKOW | `section.o-article-content` 2. br | — | 1. br = AI summary Bankier |
| 17 | INPRO | `table.seauid2` | — | |
| 18 | MBANK | `table.seauid2` | — | |
| 19 | FORBUILD | `table.seauid2` | — | |
| 20 | GKSKAT | `table.seauid2` | **4 PDFy** | |
| 21 | TRANSPOL | `table.seauid2` | — | |
| 22 | GALVO | `table.seauid2` | — | |
| 23 | BKDGAMES | — | **5 PDFów** | EBI raport roczny — brak seauid2 |
| 24 | GKSKAT | `table.seauid2` | **5 PDFów** | |
| 25 | ONDE | `table.seauid2` | — | |

**Podsumowanie struktury treści:**

| Źródło treści | Liczba | % | Uwagi |
|---|---|---|---|
| `table.seauid2 tbody` | 20 | 80% | PRIMARY — kanon ESPI/EBI na Bankier.pl |
| `section.o-article-content` 2. `<br>` | 2 | 8% | ESPI bez tabeli (1. br = AI Bankier) |
| Tylko PDF (brak seauid2) | 3 | 12% | EBI raporty roczne (PLOTTWIST, MERA, BKDGAMES) |

**Załączniki PDF:**

| Liczba PDFów | Ogłoszeń | % |
|---|---|---|
| 0 (treść w HTML) | 13 | 52% |
| 1 PDF | 5 | 20% |
| 2–5 PDFów | 7 | 28% |

> **Kluczowe odkrycie:** Wiele ogłoszeń ma wiele załączników PDF (max 5 w próbce).
> S-02 musi zbierać WSZYSTKIE linki `.pdf` per ogłoszenie, nie tylko pierwszy.

**PDF format (pypdf):**
- 0/25 skanów — wszystkie PDFy text-selectable
- Rozmiary: 4 KB – 732 KB w próbce

---

## OCR Decision (OQ-2 — RESOLVED)

**Decyzja: HTML fallback. Bez pytesseract w MVP.**

**Uzasadnienie:**
- 0/25 ogłoszeń to skany (0% < próg 20%)
- `pypdf` poprawnie ekstrahuje tekst ze wszystkich PDF-ów w próbce
- `table.seauid2 tbody` jest dostępne dla 80% ogłoszeń — nawet gdy PDF jest skanem, HTML zawiera treść
- Dodanie pytesseract zwiększyłoby złożoność i czas buildu bez uzasadnionej potrzeby

**Ryzyko:** Próbka jednego dnia. Jeśli PDF jest skanem i brak `table.seauid2`, S-02
powinien gracefully fallback do `section.o-article-content` zamiast zwracać pusty tekst.

---

## Metadata Schema (per ogłoszenie)

Pola zwracane przez pipeline na podstawie Phase 1 i struktury `oldProjectData/bankier.py`:

```python
@dataclass
class Announcement:
    title: str           # pełny tytuł z .m-quotes-announcements-item__anchor
    espi_code: str       # prefix tytułu przed ":" (kod raportowy ESPI)
    bankier_url: str     # URL strony ogłoszenia na bankier.pl
    date: datetime.date
    pub_time: datetime.time | None
    source: str          # "espi" | "ebi"
    # Pola z 2-hop (opcjonalne w MVP S-01):
    ticker: str | None   # ticker giełdowy, np. "SFK" (z nawiasów w profilu)
    company: str | None  # pełna nazwa, np. "Polkap SA w restrukturyzacji"
```

---

## Notes for S-01 (scraper-dedup)

1. **Primary selector:** `.m-quotes-announcements-item` — stabilny, 25 elementów per strona
2. **Link + tytuł:** `.m-quotes-announcements-item__anchor` — jeden selektor daje oba pola
3. **Data:** format `DD.MM.YYYY HH:MM` — parser: `datetime.strptime(s, "%d.%m.%Y %H:%M")`
4. **Ticker 2-hop jest kosztowny** — S-01 powinien zdecydować czy pobierać ticker na etapie listingu czy odkładać do S-02/S-03. Rekomendacja MVP: odkładać (ticker można wyciągnąć z espi_code lub pominąć do czasu S-03)
5. **Rate limiting:** `time.sleep(0.5)` przed każdym requestem — Bankier.pl toleruje taki interwał
6. **Paginacja URL:** `/komunikaty-spolek/1`, `/2`, `/3`… — suffix numeryczny. Kontynuuj dopóki ostatni (najstarszy) item na stronie mieści się w oknie 15 min; zatrzymaj się gdy wyjdzie poza.
7. **Dedup key:** numeryczny ID z końca URL (np. `9140373`) — sekwencyjny i unikalny. BQ lookup po tym ID przed przetworzeniem (zależność F-02).
8. **Diagnostyki** z `oldProjectData/bankier.py` (diag dict) warto zachować dla debugowania
9. **Zmiana vs stary bankier.py:** selektor anchora zmieniony, ticker 2-hop zamiast inline

## Notes for S-02 (content-parser)

### Strategia ekstrakcji treści (priorytet malejący)

```
1. PRIMARY:   table.seauid2 tbody
              → 80% ogłoszeń; kanon ESPI/EBI; ustrukturyzowana treść oficjalnego komunikatu

2. PDFs:      WSZYSTKIE a[href$='.pdf'] na stronie ogłoszenia
              → do 5 załączników per ogłoszenie; filtr BLOCKED_KEYWORDS
              → pypdf.PdfReader(io.BytesIO(data)).pages — text-selectable w 100% próbki

3. FALLBACK:  section.o-article-content — DRUGI blok <br>
              → 8% ogłoszeń (ESPI bez seauid2); pierwszy <br> = AI summary Bankier — pomiń
```

### Szczegółowe notatki

1. **`table.seauid2 tbody` to PRIMARY** — nie "fast-path dla KNF" jak w `oldProjectData/content_parser.py`, lecz główny kontener treści ESPI/EBI dla 80% ogłoszeń. Zachować jako pierwsze sprawdzenie.
2. **Wiele PDFów per ogłoszenie** — max 5 w próbce (NOVITA, BKDGAMES, GKSKAT). S-02 musi zbierać listę wszystkich linków `.pdf`, nie tylko pierwszego.
3. **PDF host:** `bonnier.pl/static/att/{ebi|emitent}/YYYY-MM/...` — oba subpath hostują PDFy
4. **Rozmiar PDF:** 4–732 KB w próbce — httpx timeout 30s wystarczy
5. **`section.o-article-content` struktura:** 2 bloki `<br>` — pierwszy to AI summary generowany przez Bankier.pl, drugi to treść właściwa komunikatu; brać zawsze **drugi**
6. **BLOCKED_KEYWORDS** przy szukaniu `.pdf`: `regulamin, cookies, polityka, rodo, statut`
7. **EBI raporty roczne** (PLOTTWIST, MERA, BKDGAMES) mogą nie mieć `table.seauid2` — jedyna treść to PDFy
8. **OCR:** niepotrzebne — 0% skanów w próbce; gdy `pypdf` zwraca pusty tekst → fallback do HTML
9. **pypdf:** `PdfReader(io.BytesIO(data))` + pętla po `reader.pages` (nie tylko `pages[0]`)

---

## Reference Code

| Komponent | Plik | Kluczowe linie |
|---|---|---|
| Scraper (lista) | `oldProjectData/bankier.py` | `collect_bankier()` linie 45–193 |
| Ticker extraction | `oldProjectData/bankier.py` | linie 244–253 (nieaktualny — wymaga 2-hop) |
| PDF discovery | `oldProjectData/content_parser.py` | `discover_announcement()` linie 307–325 |
| HTML text extraction | `oldProjectData/content_parser.py` | `fetch_files()` linie 328–371 |
| Fast-path KNF | `oldProjectData/content_parser.py` | linie 29–51 |
| HTTP client | `oldProjectData/base.py` | `get()`, `download_binary()` z retry logic |
| Research scraper check | `scripts/research/bankier_html_check.py` | Phase 1 |
| Research PDF sampler | `scripts/research/pdf_sampler.py` | Phase 2 |
