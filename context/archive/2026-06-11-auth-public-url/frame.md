# Frame Brief: Auth + Public URL (PUL-17)

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

Recenzenci 10xBuilder mogą odrzucić projekt bez widocznego mechanizmu access
control. GCP IAM chroni pipeline, ale nie jest widoczne dla zewnętrznego
recenzenta. Formularz certyfikacyjny wymaga konkretnego publicznego URL-a.

## Initial Framing (preserved)

- **User's stated cause or approach**: GCP IAM jest niewidoczny dla recenzenta; potrzebny jawny X-API-Key w kodzie.
- **User's proposed direction**: Dodać GET /health (publiczny), GET /announcements (X-API-Key), DELETE /announcements/{id} (X-API-Key); deploy jako Cloud Run Service; API key w Secret Manager.
- **Pre-dispatch narrowing**: Oba wymagania równorzędne — widoczna autentykacja I publiczny URL są twardymi warunkami certyfikacji.

## Dimension Map

Obserwacja mogła być źle uframowana w następujących wymiarach:

1. **Auth widoczność** — czy recenzent patrzy na działający endpoint czy na KOD? (GCP IAM wystarczy do bezpieczeństwa, ale X-API-Key jest widocznym artefaktem w repozytorium)
2. **Cloud Run Service** — jedyny sposób na trwały publiczny URL ← framing poprawny
3. **DELETE endpoint** — czy DELETE jest wymogiem certyfikacyjnym, czy scope creep pod "brakujący CRUD"? ← hipoteza do zbadania
4. **BQ read integracja** — GET /announcements wymaga BQ odczytu; jak głęboka integracja wchodzi w zakres PUL-17?

## Hypothesis Investigation

| Hipoteza | Dowody | Werdykt |
| --- | --- | --- |
| Cloud Run Service wymagany dla publicznego URL | infra.md: tylko Jobs istnieją; Service = trwały HTTP endpoint — jedyna droga do publicznego URL | **STRONG** |
| X-API-Key wystarczy jako widoczna autentykacja | tech-stack.md: `has_auth: false`; prd.md: brak auth w MVP — X-API-Key to minimalny, czytelny dla recenzenta mechanizm | **STRONG** |
| DELETE endpoint wymagany dla certyfikacji | prd.md: brak wzmianki o CRUD/DELETE/admin API; roadmap.md: S-01–S-05 done, PUL-17 bez wymagania DELETE; test-plan.md: brak API endpointów = gap medium, nie wymóg; Issue PUL-17: "uzupełnia też brakujący Delete" — secondary | **NONE** |
| GET /announcements wymaga głębokiej BQ integracji | db/bigquery.py: `announcement_id` jako klucz; `fetch_top_n_for_window()` już istnieje jako wzorzec; read-only query trivial | **WEAK** (prosta integracja) |

## Narrowing Signals

- Oba ryzyka (auth widoczność + publiczny URL) są równorzędnymi wymaganiami certyfikacji — potwierdzone przez użytkownika.
- Żaden dokument projektowy (PRD, roadmap, test-plan) nie wymienia DELETE ani CRUD jako wymagania certyfikacyjnego.
- FastAPI zainstalowany (`pyproject.toml:8`), ale zero kodu — żaden endpoint nie istnieje.
- `uvicorn` zainstalowany — Service może użyć tego samego obrazu Docker z innym CMD.
- `ADMIN_API_KEY` nie istnieje jeszcze w Secret Manager — wymaga stworzenia.

## Cross-System Convention

Projekt jest autonomous scheduled pipeline — brak UI/API w MVP (prd.md). Dodanie
Cloud Run Service obok istniejących Jobs to standardowy wzorzec GCP dla hybrydowych
architektur (batch + HTTP). Praktyka X-API-Key dla prostego admin API jest powszechna
i czytelna dla zewnętrznego recenzenta. DELETE rows z BQ nie narusza CLAUDE.md (dotyczy
tylko DROP TABLE, nie DML).

## Reframed (or Confirmed) Problem Statement

> **Rzeczywisty problem do zaplanowania**: Certyfikacja wymaga widocznego auth
> w kodzie + publicznego URL; minimum które to spełnia to GET /health (publiczny)
> + GET /announcements (X-API-Key), deployed jako Cloud Run Service — DELETE nie
> jest wymaganiem certyfikacyjnym i wykracza poza zakres PUL-17.

Oryginalny framing był trafny w 2/3 — Cloud Run Service i X-API-Key to słuszny
kierunek. Jedyna korekta: DELETE /announcements/{id} to scope creep niewymagany
do certyfikacji; jeśli CRUD jest celem, zasługuje na oddzielny ticket.

## Confidence

**HIGH** — silne dowody z 4 źródeł (PRD, roadmap, test-plan, treść Issue) potwierdzają
że DELETE nie jest wymogiem; Cloud Run Service + X-API-Key potwierdzone jako minimalne
i wystarczające.

## User Scoping Decisions (post-frame)

- **DELETE endpoint**: zostaje w scope mimo braku wymogu certyfikacyjnego — user decision.
- **Dockerfile strategy**: jeden obraz, dwa entrypoints. Jobs używają `main.py`/`post_main.py`;
  Service deployowany z `--command` override w deploy.yml wskazującym na nowy `api_main.py`
  (FastAPI app z uvicorn). Brak osobnego Dockerfile.

## What Changes for /10x-plan

Plan powinien obejmować: nowy `src/api.py` (FastAPI router + endpointy), nowy `api_main.py`
(uvicorn entrypoint dla Service), modyfikację `deploy.yml` (nowy blok dla Cloud Run Service),
stworzenie secretu `ADMIN_API_KEY` w Secret Manager. Endpointy: GET /health (publiczny),
GET /announcements?limit=N (X-API-Key), DELETE /announcements/{id} (X-API-Key). Jedno
żródło prawdy dla BQ queries: istniejący `db/bigquery.py`.

## References

- `pyproject.toml:8-9` — FastAPI + uvicorn zainstalowane, nieużywane
- `main.py:1-84` — obecny entry point (CLI batch, nie HTTP server)
- `Dockerfile:13` — `CMD ["uv", "run", "python", "main.py"]`
- `context/foundation/infra.md:10-13` — istniejące Cloud Run Jobs
- `context/foundation/prd.md` — brak CRUD/DELETE w wymaganiach
- `context/foundation/roadmap.md` — PUL-17 bez wymagania DELETE
- `db/bigquery.py` — schemat `announcements`, istniejące query patterns
