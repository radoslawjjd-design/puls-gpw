---
change_id: refactor-opportunities
title: Refactor opportunities — ranking kandydatów z analizy przepływu lotów
status: research
created: 2026-08-07
updated: 2026-08-07
tracking:
  linear: null
  github: null
---

## Notes

Zmiana wykonana w ramach ścieżki 10xArchitect (M4L4, element ④ raportu). **Bez ticketu
Linear i bez issue na GitHubie** — ćwiczenie certyfikacyjne, nie pozycja z backlogu.

### Intencja

Mamy analizę tego repozytorium, która dokumentuje dług techniczny i ryzyka strukturalne:
`context/changes/portfolio-lots-flow/research.md`. Ta zmiana odpowiada na pytanie, które
tamta analiza celowo zostawiła otwarte: **KTÓRE z tych problemów warto naprawić, w jakim
docelowym kształcie i w jakiej kolejności.**

Eksplorujemy każdy zapisany problem w kodzie i historii, a potem porządkujemy je jako
*refactor opportunities*.

Zmiana przebiega etapami: **eksploracja → decyzja i plan → (implementacja)**.
Na etapie eksploracji **nie dzieje się żaden refaktor i nie zapada żadna decyzja.**

Wynik eksploracji: `research.md` tej zmiany, zakończony rankingiem opcji z trade-offami.
Najpierw czytamy raport; decyzja, co realizujemy, zapada na etapie planowania w `/10x-plan`,
a refaktor rusza dopiero według przyjętego planu.

**Dla ścieżki Architekta implementacja nie jest wymagana** — M4L4 kończy się na planie
(„Kończysz na planie i przekazaniu go do implementacji").

### Wejście

Problemy T1–T8 z `portfolio-lots-flow/research.md`, plus sekcja „dług tani" (to, czego
świadomie nie zaliczyliśmy do długu) jako wejście do oceny wykonalności i kosztu.
Priory: `context/map/repo-map.md`, `context/map/artifact-2-structure.md`.
