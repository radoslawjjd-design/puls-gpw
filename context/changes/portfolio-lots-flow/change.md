---
change_id: portfolio-lots-flow
title: Deep Focus — przepływ FIFO-lotów portfela (analiza, bez zmian w kodzie)
status: research
created: 2026-08-07
updated: 2026-08-07
tracking:
  linear: null
  github: null
---

## Notes

Zmiana **research-only**, wykonana w ramach ścieżki 10xArchitect (M4L3, element ② i ③
raportu architektonicznego). Świadomie **bez ticketu Linear i bez issue na GitHubie** —
to ćwiczenie certyfikacyjne, nie pozycja z backlogu produktowego. Stąd `tracking: null`
po obu stronach.

**Zakres: wyłącznie analiza i opis stanu obecnego. Żadnych zmian w kodzie.**

### Cel wybrany z mapy

Badam **przepływ FIFO-lotów portfela**: od zdarzenia kupna/sprzedaży (ręczny wpis albo
import z XTB), przez księgę lotów w `src/portfolio_lots.py`, do wyliczeń, które z niej
korzystają — podstawa kosztowa, wycena historyczna, zyski zrealizowane.

**Entry pointy** (z sekcji „Pierwszy dzień" mapy → `src/api.py`):
- `POST /api/portfolio/positions` (`src/api.py:1142`)
- `POST /api/portfolio/import/commit` (`src/api.py:1435`)
- `GET /api/portfolio/history` (`src/api.py:1366`), `GET /api/portfolio/realized` (`:1516`)

**Dlaczego mapa wskazała ten obszar** — trzy strefy ryzyka przecinają się dokładnie tutaj:

| Strefa ryzyka z `repo-map.md` | Jak dotyczy tego przepływu |
| -- | -- |
| #1 `src/api.py` ↔ `db/bigquery.py` (51 nazw, 62 łatki w conftest) | endpointy portfela wołają warstwę danych bezpośrednio |
| #3 `db/bigquery.py` — 106 funkcji, 4 importy w górę | jedna z tych funkcji liczy podstawę kosztową |
| #4 **dryf dokumentacji od kodu** | nieudokumentowane naruszenie warstwy to **dokładnie ten moduł**: `db/bigquery.py:43` importuje `src.portfolio_lots`, a komentarz w `tach.toml` o tym nie wie |

Ostatni wiersz jest powodem, dla którego wybrałem ten przepływ, a nie inny. Mapa znalazła
rozjazd dokumentacji; ten research sprawdza, co ten rozjazd zasłania.

### Unknowns odziedziczone z sekcji „Ograniczenia" mapy

- graf pokazuje **importy, nie wywołania** — czy `db.bigquery` realnie wywołuje wszystkie
  zaimportowane nazwy z `portfolio_lots`;
- front (`static/index.html`, 6326 linii) jest strukturalnie białą plamą — nie wiadomo,
  którędy wyniki tego przepływu docierają do UI;
- historia mierzy zmiany, nie użycie.

### Wynik

`context/changes/portfolio-lots-flow/research.md` z dwiema jawnymi sekcjami:
**Feature overview** i **Technical debt**, z rygorem evidence / inference / unknown,
zweryfikowany `ast-grep` (krok 3 z M4L3).
