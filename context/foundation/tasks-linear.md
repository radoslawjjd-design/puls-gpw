# Tasks — Linear mirror

> Linear jest źródłem prawdy dla statusów, zależności i etykiet.
> Workspace: https://linear.app/puls-gpw
> Team: **Puls-gpw** (ID: `a1411620-1a72-4984-937a-db08492f8f93`)
> Źródło prawdy dla zależności i kontekstu: `context/foundation/roadmap.md`
>
> Aktualizuj status w Linear gdy zaczynasz / kończysz pracę nad issue.
> `tasks-github.md` jest zdeprecjonowany — Linear go zastąpił.

## Milestone: MVP

| Roadmap ID | Linear | GitHub | Tytuł | Labels | Status |
|---|---|---|---|---|---|
| F-01 | [PUL-5](https://linear.app/puls-gpw/issue/PUL-5) | [#1](https://github.com/radoslawjjd-design/puls-gpw/issues/1) | Zbadaj HTML Bankier.pl i próbki PDF ESPI/EBI | `foundation` `ready` | In Progress |
| F-02 | [PUL-6](https://linear.app/puls-gpw/issue/PUL-6) | [#2](https://github.com/radoslawjjd-design/puls-gpw/issues/2) | BigQuery schema announcements + klient Python | `foundation` `ready` | Todo |
| F-03 | [PUL-7](https://linear.app/puls-gpw/issue/PUL-7) | [#3](https://github.com/radoslawjjd-design/puls-gpw/issues/3) | Structured logging + email alert na błąd pipeline'u | `foundation` `ready` | Todo |
| S-01 | [PUL-8](https://linear.app/puls-gpw/issue/PUL-8) | [#4](https://github.com/radoslawjjd-design/puls-gpw/issues/4) | Scraper Bankier.pl + dedup check BigQuery | `slice` `proposed` | Backlog |
| S-02 | [PUL-9](https://linear.app/puls-gpw/issue/PUL-9) | [#5](https://github.com/radoslawjjd-design/puls-gpw/issues/5) | Parser PDF (pypdf) i HTML fallback | `slice` `proposed` | Backlog |
| S-03 | [PUL-10](https://linear.app/puls-gpw/issue/PUL-10) | [#6](https://github.com/radoslawjjd-design/puls-gpw/issues/6) | Analiza Gemini + supervisor gate (max 3 próby) | `slice` `proposed` | Backlog |
| S-04 | [PUL-11](https://linear.app/puls-gpw/issue/PUL-11) | [#7](https://github.com/radoslawjjd-design/puls-gpw/issues/7) | Email notifier + orchestracja Cloud Run Job | `slice` `proposed` | Backlog |

## Tooling

| Roadmap ID | Linear | GitHub | Tytuł | Labels | Status |
|---|---|---|---|---|---|
| T-01 | [PUL-12](https://linear.app/puls-gpw/issue/PUL-12) | [#8](https://github.com/radoslawjjd-design/puls-gpw/issues/8) | Automatyzacja sync GitHub Issues ↔ Linear | `foundation` `proposed` | Backlog |

## Zależności

```
PUL-5 F-01 ──┬──► PUL-8 S-01 ──► PUL-9 S-02 ──► PUL-10 S-03 ──► PUL-11 S-04
PUL-6 F-02 ──┘              └──────────────────► PUL-10 S-03
PUL-7 F-03 ──────────────────────────────────────────────────────► PUL-11 S-04
```

Zależności są ustawione jako relacje `blockedBy` w Linear — Linear automatycznie sygnalizuje zablokowane issues.

## Etykiety

| Label | Kolor | Znaczenie |
|---|---|---|
| `foundation` | `#0EA5E9` (niebieski) | Prereq task odblokujący slice'y |
| `slice` | `#8B5CF6` (fioletowy) | Pionowy slice — user-visible outcome |
| `ready` | `#22C55E` (zielony) | Prerequisites spełnione, można zaczynać |
| `proposed` | `#94A3B8` (szary) | Zdefiniowany, czeka na prereqs |

## Dostępne statusy w Linear (team Puls-gpw)

| Status | Typ | Kiedy używać |
|---|---|---|
| Backlog | backlog | Zdefiniowany, prereqs niespełnione |
| Todo | unstarted | Prereqs spełnione, gotowy do podjęcia |
| In Progress | started | Aktywna praca |
| In Review | started | PR otwarty, czeka na review |
| Done | completed | Zakończony |
| Canceled | canceled | Porzucony |
| Duplicate | duplicate | Duplikat innego issue |

## Workflow

Gdy zaczynasz pracę nad issue:
1. Zmień status `Todo` → `In Progress` w Linear
2. Zmień label `proposed` → `ready` jeśli prereqs właśnie spełnione
3. Po zakończeniu: zamknij issue w GitHub + zmień status → `Done` w Linear
4. Uruchom `/10x-archive` dla danego change folderu

## Decyzja architektoniczna

**Linear = source of truth** dla statusów i zależności (zastąpił `tasks-github.md`).
GitHub Issues pozostają jako code-level tracking z linkami do Linear (attachmenty dodane do każdego PUL-X).
`roadmap.md` pozostaje ręczny — to doc projektowy, nie tracker statusów.
Synchronizacja jest jednostronna: zmiany statusu robisz w Linear, nie w GitHub.
