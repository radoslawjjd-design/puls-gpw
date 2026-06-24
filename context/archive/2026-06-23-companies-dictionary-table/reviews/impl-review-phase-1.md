<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Companies Dictionary Table (ticker, name, hop_url, isin)

- **Plan**: context/changes/companies-dictionary-table/plan.md
- **Scope**: Phase 1 of 4
- **Date**: 2026-06-23
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Summary

`_COMPANIES_SCHEMA`, `create_companies_table_if_not_exists()`, `ensure_companies_schema_current()`,
and `upsert_company()` (`db/bigquery.py:463-535`) match the plan's contract exactly — schema
fields, signature, MERGE SQL shape, and error handling all mirror the watchlist/portfolio_snapshots
precedent. The MERGE binds all four values as scalar parameters (no injection risk), no
reserved-keyword columns among the 6 schema fields, no scope creep. Unit tests
(`tests/test_bigquery.py:800-845`) mock `db.bigquery._get_client` only, matching house style.

Automated checks (unit tests + lint) and manual BigQuery round-trip (insert + update paths,
real `puls-gpw.espi_ebi.companies` dataset) both passed with observable evidence. No findings
to triage.

Committed at `9bdb683`.

## Findings

None.
