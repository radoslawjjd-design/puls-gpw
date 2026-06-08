<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: X-post Generation + Email Delivery

- **Plan**: context/changes/xpost-generation/plan.md
- **Scope**: All Phases (0–4)
- **Date**: 2026-06-08
- **Verdict**: NEEDS ATTENTION → resolved via triage
- **Findings**: 0 critical  4 warnings  4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | WARNING |

## Findings

### F1 — Pydantic validation brakowało w post_generator.py

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Pattern Consistency
- **Location**: src/post_generator.py:141–156
- **Detail**: Reguła w .claude/rules/gemini-ai.md wymaga walidacji Pydantic przed supervisorem. Implementacja używała ręcznego isinstance check bez Pydantic.
- **Fix**: Dodano `class _PostResponse(BaseModel): tweets: list[str]` + `_PostResponse.model_validate(data)` w try/except; oddzielny `except ValidationError`.
- **Decision**: FIXED via Fix A

### F2 — save_post_text nie sprawdzało num_dml_affected_rows

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:337–370
- **Detail**: Wszystkie sibling DML functions mają `if job.num_dml_affected_rows == 0: raise BigQueryError(...)` — save_post_text nie miało. Silent data loss jeśli announcement_ids nie istnieją w BQ.
- **Fix**: Dodano check `if job.num_dml_affected_rows == 0: raise BigQueryError(...)` po `job.result()`.
- **Decision**: FIXED

### F3 — 3 testy w test_scraper.py nie przechodziły

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Success Criteria
- **Location**: tests/test_scraper.py (test_parse_item_fields, test_dedup_filter, test_stop_condition_on_page)
- **Detail**: Commit b323713 (post-epilogue) zmienił SCRAPE_WINDOW_MINUTES default 15→60 bez aktualizacji testów. Fixture OLDTICKER (10:00, 30 min ago) wchodziło w 60-min okno. Testy były zielone przy epilogue db283eb.
- **Fix**: Dodano `window_minutes=15` jawnie w 3 testach testujących stop condition i dedup — testy były napisane dla konkretnego zachowania okna, nie dla wartości domyślnej.
- **Decision**: FIXED

### F4 — _run_generate_post.py: brak __main__ guard + side effects na imporcie

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality / Scope Discipline
- **Location**: _run_generate_post.py:32–219
- **Detail**: Cały kod wykonywalny (BQ fetch + email send) był na poziomie modułu bez `if __name__ == "__main__":`. Dodatkowo czytał SMTP creds przez os.environ bezpośrednio zamiast przez `notifier._smtp_creds()` (omijał BOM/CRLF stripping). Naive `datetime.now()` zamiast Warsaw timezone.
- **Fix**: Przeniesiono funkcje pomocnicze na poziom modułu; cały kod wykonywalny w `if __name__ == "__main__":`. Przepisano SMTP na `_smtp_creds()`. Naprawiono naive datetime na `datetime.now(ZoneInfo("Europe/Warsaw"))`.
- **Decision**: FIXED

### F5 — Hard-coded retry count decoupled od loop bound

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Reliability
- **Location**: post_main.py:107
- **Detail**: `range(1, 4)` i `save_post_text(..., 3)` jako dwie niezależne liczby.
- **Fix**: Wyciągnięto `_MAX_ATTEMPTS = 3`; użyto w `range(1, _MAX_ATTEMPTS + 1)` i `save_post_text(ann_ids, None, _MAX_ATTEMPTS)`.
- **Decision**: FIXED

### F6 — post_main.py: guard `not tickers` zamiast `len < 2` (plan drift)

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: post_main.py (guard)
- **Detail**: Plan mówił `if len(announcements) < 2`; implementacja używa `if not tickers`. Jest to tightening — poprawione zachowanie, nie regresja.
- **Fix**: Dodano komentarz wyjaśniający dlaczego `tickers` zamiast `len`.
- **Decision**: FIXED (comment added)

### F7 — deploy.yml: `|| echo` maskowało non-"not-found" błędy gcloud

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Reliability
- **Location**: .github/workflows/deploy.yml:49
- **Detail**: `|| echo` łapało KAŻDY błąd gcloud, nie tylko "job not found". Job puls-gpw-post istnieje produkcyjnie — fallback był już zbędny.
- **Fix**: Usunięto `|| echo ...`; krok failuje twardo jeśli gcloud zwróci błąd.
- **Decision**: FIXED

### F8 — Dual ticker dedup bez komentarza

- **Severity**: 📋 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: post_main.py:84
- **Detail**: post_main.py i post_generator.py deduplikują tickery niezależnie. Poprawne, ale bez komentarza niejasne po co oba.
- **Fix**: Dodano komentarz `# Dedup tickers here for supervisor; generate_post deduplicates independently internally`.
- **Decision**: FIXED (comment added)
