<!-- PLAN-REVIEW-REPORT -->
# Plan Review: F-01: Bankier.pl HTML & PDF Research

- **Plan**: context/changes/scraper-parser-research/plan.md
- **Mode**: Deep
- **Date**: 2026-05-26
- **Verdict**: REVISE → SOUND (all findings resolved)
- **Findings**: 1 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|---|---|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | WARNING |

## Grounding

4/4 existing paths ✓ (bankier.py, content_parser.py, pyproject.toml, base.py), 7/7 symbols ✓ (.m-quotes-announcements-item, date selectors, metadata dict, _extract_ticker_hint, _find_attachments, _extract_text, table.seauid2 fast-path), brief↔plan ✓

## Findings

### F1 — Missing Progress entries for 2 success criteria bullets

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: ## Progress — Phase 1 Manual & Phase 2 Manual
- **Detail**: Phase 1 Manual Verification had 3 bullets but Progress Phase 1 Manual had only 2 entries (missing "Widoczne są: title, data, nazwa spółki, URL ogłoszenia"). Phase 2 Manual had 3 bullets but only 2 entries (missing "Odnotuj czy fast-path KNF (table.seauid2) byłby potrzebny"). /10x-implement will fail to parse a Progress section where phase criteria have no matching checkboxes.
- **Fix**: Added `- [ ] 1.4 Widoczne są: title, data, company, URL ogłoszenia` and `- [ ] 2.4 Fast-path KNF (table.seauid2) noted if applicable` to ## Progress.
- **Decision**: FIXED

### F2 — Script contracts omit User-Agent and rate limiting

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1 bankier_html_check.py contract & Phase 2 pdf_sampler.py contract
- **Detail**: Brief explicitly flagged that Bankier.pl may block automated requests. base.py sets Chrome 121 User-Agent + Accept-Language: pl-PL + 0.5s delay per request. Research scripts are standalone (no oldProjectData imports) and contracts only said "httpx.get()" — no headers or delay specified. A plain request without headers risks bot-detection page instead of announcement list, invalidating selector verification. User confirmed Bankier does not block ESPI scraping (public data) but UA + delay are required.
- **Fix**: Added exact headers (Chrome 121 UA, Accept-Language: pl-PL) and `time.sleep(0.5)` to contracts of both bankier_html_check.py and pdf_sampler.py.
- **Decision**: FIXED (Fix A)

### F3 — Phase 3 meta-checks not tracked in Progress

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3 Manual Verification & ## Progress Phase 3
- **Detail**: Phase 3 success criteria included two meta-checks ("S-01/S-02 może być uruchomiony bez dodatkowych pytań") with no matching Progress entries. These are fully implied by 3.1–3.4 — a subjective checkbox adds no measurable verification.
- **Fix**: Removed the two redundant bullets from Phase 3 success criteria.
- **Decision**: FIXED
