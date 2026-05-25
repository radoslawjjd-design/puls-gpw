# Tasks — GitHub Issues mirror

> Plik synchronizowany ręcznie z GitHub Issues.
> Źródło prawdy dla statusów: https://github.com/radoslawjjd-design/puls-gpw/issues
> Źródło prawdy dla zależności i kontekstu: `context/foundation/roadmap.md`
>
> Aktualizuj status (open → closed) gdy zamykasz issue w GitHub.

## Milestone: MVP

| Roadmap ID | # | Tytuł | Labels | Status |
|---|---|---|---|---|
| F-01 | [#1](https://github.com/radoslawjjd-design/puls-gpw/issues/1) | Zbadaj HTML Bankier.pl i próbki PDF ESPI/EBI | `foundation` `ready` | open |
| F-02 | [#2](https://github.com/radoslawjjd-design/puls-gpw/issues/2) | BigQuery schema announcements + klient Python | `foundation` `ready` | open |
| F-03 | [#3](https://github.com/radoslawjjd-design/puls-gpw/issues/3) | Structured logging + email alert na błąd pipeline'u | `foundation` `ready` | open |
| S-01 | [#4](https://github.com/radoslawjjd-design/puls-gpw/issues/4) | Scraper Bankier.pl + dedup check BigQuery | `slice` `proposed` | open |
| S-02 | [#5](https://github.com/radoslawjjd-design/puls-gpw/issues/5) | Parser PDF (pypdf) i HTML fallback | `slice` `proposed` | open |
| S-03 | [#6](https://github.com/radoslawjjd-design/puls-gpw/issues/6) | Analiza Gemini + supervisor gate (max 3 próby) | `slice` `proposed` | open |
| S-04 | [#7](https://github.com/radoslawjjd-design/puls-gpw/issues/7) | Email notifier + orchestracja Cloud Run Job | `slice` `proposed` | open |

## Zależności

```
#1 F-01 ──┬──► #4 S-01 ──► #5 S-02 ──► #6 S-03 ──► #7 S-04
#2 F-02 ──┘         └──────────────────► #6 S-03
#3 F-03 ──────────────────────────────────────────► #7 S-04
```

## Workflow

Gdy zaczynasz pracę nad issue:
1. Zmień label `proposed` → `ready` (jeśli prerequisites spełnione)
2. Przypisz issue do siebie
3. Po zakończeniu: zamknij issue + zaktualizuj status w tej tabeli + `/10x-archive`
