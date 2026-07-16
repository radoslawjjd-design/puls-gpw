---
date: 2026-07-16T00:00:00+02:00
researcher: Radek
git_commit: 280ce0aa03b3e44740b9a62df997b9605703645b
branch: pul-56-ai-security-review-pipeline
repository: puls-gpw
topic: "AI security review pipeline — dedicated CI gate (PUL-56)"
tags: [research, ci-cd, github-actions, gemini, security, ai-reviewer]
status: complete
last_updated: 2026-07-16
last_updated_by: Radek
---

# Research: AI Security Review Pipeline — Dedicated CI Gate

**Date**: 2026-07-16  
**Git Commit**: 280ce0aa  
**Branch**: pul-56-ai-security-review-pipeline  
**Repository**: puls-gpw

## Research Question

Zbadaj istniejący pattern AI code-review (workflow + composite action + tool package) by zrozumieć co dokładnie trzeba zmieniować/stworzyć dla PUL-56: dedykowanego AI security review gate'u na PRach do mastera.

---

## Summary

Istniejący `ai-code-review.yml` + `ai-reviewer` action + `tools/ai-code-reviewer/` stanowią bardzo spójny i dobrze udokumentowany wzorzec. PUL-56 polega na stworzeniu **3 nowych artefaktów** (workflow YML, composite action, tool package) przez przekalkowanie istniejącego wzorca z 5 klasami zmian:

1. Ciągi tekstowe: `ai-cr:*` → `ai-sec:*`, `ai-code-review` → `ai-security-review`, marker HTML
2. Ścieżki: `ai-reviewer` → `ai-security-reviewer`, `ai-code-reviewer` → `ai-security-reviewer`
3. Logika gate'u: obecna wymaga `verdict==pass AND min_score≥4`; dla security gate mamy wybór (patrz Decyzje Otwarte)
4. `instructions.ts`: całkowicie nowe kryteria security-focused (zamiast 6 kryteriów code-quality)
5. `schema.ts`: nowe klucze criterionów odpowiadające security criteria

Kod infrastrukturalny (`agent.ts`, `review.ts`, `input.ts`) można przekalkować prawie bez zmian.

---

## Detailed Findings

### 1. Workflow — `.github/workflows/ai-code-review.yml`

**Plik**: `.github/workflows/ai-code-review.yml` (302 linie)

**Trigger** (linie 31–38):
```yaml
on:
  pull_request:
    branches: [master]
    types: [opened, synchronize, reopened, labeled]
  workflow_dispatch:
```

**Concurrency** (linia 45):
```yaml
group: ai-code-review-${{ github.event.pull_request.number || github.ref }}
cancel-in-progress: true
```
→ Dla nowego workflow: `ai-security-review-${{ ... }}`

**Fork guard** (linia 54–56):
```yaml
if: >-
  github.event_name == 'workflow_dispatch' ||
  github.event.pull_request.head.repo.full_name == github.repository
```
→ Identyczny w nowym workflow.

**Guard step** (linie 71–95): Filtruje zdarzenia `labeled` — odpala ponownie tylko gdy label = `ai-cr:review` lub `ai-cr:override`. Dla nowego: `ai-sec:review` / `ai-sec:override`.

**Diff step** (linie 120–138): Wyklucza `uv.lock`, `*.lock`, `tach_module_graph.dot`. Identyczny w nowym workflow.

**Agent step** (linie 144–151): Wywołuje `uses: ./.github/actions/ai-reviewer`. W nowym: `uses: ./.github/actions/ai-security-reviewer`.

**Labels** (linia 163–166):
```
ai-cr:passed / ai-cr:failed / ai-cr:review / ai-cr:override
```
→ Dla nowego: `ai-sec:passed / ai-sec:failed / ai-sec:review / ai-sec:override`

**Marker** (linia 184): `<!-- ai-code-review -->` → `<!-- ai-security-review -->`

**Status context** (linia 260): `"ai-code-review/verdict"` → `"ai-security-review/verdict"`

**Gate rule** (linie 288–300):
```bash
if [ "$VERDICT" = "pass" ] && [ "${MIN_SCORE:-0}" -ge 4 ]; then
  post_status success ...
```
→ Decyzja projektowa: utrzymać tę samą regułę czy uprościć do samego `verdict`? (patrz Decyzje Otwarte)

---

### 2. Composite Action — `.github/actions/ai-reviewer/action.yml`

**Plik**: `.github/actions/ai-reviewer/action.yml` (90 linii)

**Inputs**:
- `pr-title` (required)
- `pr-body` (optional, default: `""`)
- `diff-path` (required)
- `model` (optional, default: `"gemini-2.5-flash"`)

**Outputs**:
- `result` — pełny JSON z ReviewResult
- `verdict` — `"pass"` lub `"fail"`
- `min-score` — `Math.min(...Object.values(scores))`

**Steps sequence**:
1. `actions/setup-node` z `node-version-file: tools/ai-code-reviewer/.nvmrc` (linia 45)
2. `npm ci` w `working-directory: tools/ai-code-reviewer`
3. `npm run build` w `working-directory: tools/ai-code-reviewer`
4. Run: `node "${{ github.workspace }}/tools/ai-code-reviewer/dist/review.js"` (linia 71)

→ Nowy action: zamienić wszystkie ścieżki `ai-code-reviewer` → `ai-security-reviewer`.

**Verdict/min-score extraction** (linie 76–77): używa `node -e` inline do parsowania JSON ze stdin. Identyczny w nowym action.

**GITHUB_OUTPUT heredoc** (linie 85–89): delimiter `__AI_CR_RESULT__` → zmienić na `__AI_SEC_RESULT__` (good practice by uniknąć kolizji gdyby oba actiony były w jednym workflow).

---

### 3. Tool Package — `tools/ai-code-reviewer/`

#### Pliki do SKOPIOWANIA (prawie bez zmian)

**`package.json`**: dependencies identyczne:
```json
{
  "name": "ai-security-reviewer",
  "dependencies": {
    "@ai-sdk/google-vertex": "4.0.145",
    "ai": "6.0.205",
    "json5": "2.2.3",
    "zod": "4.4.3"
  }
}
```

**`tsconfig.json`**: identyczny

**`src/review.ts`** (32 linie): CLI glue — czyta env vars, wywołuje `buildReviewPrompt` + `runReview`, emituje JSON do stdout. **Identyczny** w nowym package (entry point `dist/review.js`).

**`src/input.ts`** (91 linii):
- `STRIP_PATTERNS` (linie 17–21): identyczne wzorce do wykluczenia lockfile'ów
- `stripGeneratedHunks()`: identyczna logika
- `buildReviewPrompt()`: identyczna — sekcja `UNTRUSTED DATA` + tag `<diff>`

**`src/agent.ts`** (82 linie):
- `DEFAULT_MODEL = "gemini-2.5-flash"` — identyczny
- `STEP_CAP = 8` — identyczny
- `createReviewAgent()` — identyczny (`ToolLoopAgent` + `Output.object` + `ReviewResultSchema` → zmieni się importowany schema)
- `runReview()` — identyczny (JSON5 fallback na `NoObjectGeneratedError`)

#### Pliki NOWE (core differentiators)

**`src/schema.ts`** — nowe klucze criterionów. Zamiast:
```
correctness / idiomaticity / complexity / testCoverageVsRisk / security / dataInfraSafety
```
Security reviewer potrzebuje kryteriów security-focused. Propozycja (do finalizacji w planie):
```
secretsLeakage / injectionRisk / inputValidation / dependencySafety / authPermissions / transportSecurity
```
lub inny zestaw odpowiadający ticket description: *hardcoded secrets, injection risks, missing input validation, unsafe dependency changes, auth/permission regressions*.

**`src/instructions.ts`** — security-focused prompt zamiast code-quality. Utrzymuje:
- Trust boundary preamble (UNTRUSTED DATA) — **obowiązkowe**
- Output contract (scores/verdict/summary) — **identyczne**
- Nowe kryteria security: odpowiadające nowym kluczom w schema.ts

---

### 4. Test Suite — `tools/ai-code-reviewer/test/`

Cztery pliki testów:

| Plik | Co testuje |
|---|---|
| `schema.test.ts` | Walidacja ReviewResultSchema (range, typy, missing keys) |
| `instructions.test.ts` | Kluczowe ciągi w REVIEW_INSTRUCTIONS (BQ backtick, json5, secrets, human-only) |
| `agent.test.ts` | *(nie czytany — prawdopodobnie unit testy createReviewAgent/parseReviewResult)* |
| `input.test.ts` | *(nie czytany — prawdopodobnie stripGeneratedHunks + buildReviewPrompt)* |

Dla nowego package: analogiczne 4 pliki, szczególnie `instructions.test.ts` powinien asertować kluczowe security ciągi (np. `"injection"`, `"hardcoded"`, `"authentication"`, `"dependency"`).

---

### 5. Lokalna `security-review` skill — punkt startowy prompta

Ticket mówi że `/security-review` local skill może być startem dla prompta. Sprawdzono — **nie istnieje w `.claude/skills/`**. Jest to built-in skill Claude Code (widoczna w `system-reminder`). Jej kryteria security review można wywołać lokalnie jako input do tworzenia `instructions.ts`, ale nie ma pliku do skopiowania.

---

## Code References

- `.github/workflows/ai-code-review.yml:45` — concurrency group pattern
- `.github/workflows/ai-code-review.yml:71-95` — Guard step (label filter logic)
- `.github/workflows/ai-code-review.yml:120-138` — Diff computation + excludes
- `.github/workflows/ai-code-review.yml:144-151` — Agent step (uses composite action)
- `.github/workflows/ai-code-review.yml:156-166` — Ensure labels (idempotent `--force`)
- `.github/workflows/ai-code-review.yml:168-219` — Post/update marker-tagged comment
- `.github/workflows/ai-code-review.yml:246-301` — Merge gate (`if: always()`, fail-closed)
- `.github/actions/ai-reviewer/action.yml:44-45` — Node setup with `.nvmrc`
- `.github/actions/ai-reviewer/action.yml:71` — CLI entry point path
- `.github/actions/ai-reviewer/action.yml:76-77` — Inline node JSON parsing
- `.github/actions/ai-reviewer/action.yml:85-89` — heredoc output pattern
- `tools/ai-code-reviewer/src/agent.ts:11` — DEFAULT_MODEL
- `tools/ai-code-reviewer/src/agent.ts:21` — STEP_CAP = 8
- `tools/ai-code-reviewer/src/agent.ts:34-46` — createReviewAgent() 
- `tools/ai-code-reviewer/src/agent.ts:66-82` — runReview() + JSON5 fallback
- `tools/ai-code-reviewer/src/instructions.ts:12-91` — REVIEW_INSTRUCTIONS (full prompt)
- `tools/ai-code-reviewer/src/schema.ts:14-33` — ReviewResultSchema (6 criteria)
- `tools/ai-code-reviewer/src/review.ts:13-26` — CLI main() 
- `tools/ai-code-reviewer/src/input.ts:17-21` — STRIP_PATTERNS
- `tools/ai-code-reviewer/src/input.ts:29-61` — stripGeneratedHunks()
- `tools/ai-code-reviewer/src/input.ts:69-91` — buildReviewPrompt()
- `tools/ai-code-reviewer/test/instructions.test.ts:1-49` — Pattern dla analogicznych testów
- `tools/ai-code-reviewer/test/schema.test.ts:1-62` — Pattern dla analogicznych testów

---

## Architecture Insights

### Separacja warstw

```
workflow YML          ← orchestracja + side-effects (labels, comments, status)
composite action      ← build + run + output extraction
tool package (Node)   ← LLM call + structured output + JSON5 fallback
```

Każda warstwa jest niezależna i testowalną w izolacji. Nowy security reviewer zachowuje identyczną separację.

### Dlaczego `if: always()` na merge gate

Linia 247 workflow: `if: always() && steps.guard.outputs.should_review == 'true' && github.event_name == 'pull_request'`. Bez `always()` gdy agent step failuje, subsequent steps by się pomijały i gate nigdy by nie ustawił statusu — PR wisiałby bez sygnału. `always()` + sprawdzenie `steps.review.outcome` implementuje **fail-closed** semantykę.

### Heredoc output pattern

Linie 85-89 action.yml: multiline JSON przez heredoc delimiter zamiast `echo "result=$JSON"` — konieczne bo JSON może zawierać `"` i `\n`. Pattern `<<__AI_CR_RESULT__` jest idiomem GitHub Actions dla bezpiecznych multiline output values.

### JSON5 fallback w agent.ts

Gemini ~14% produkuje trailing comma w JSON (per `context/foundation/lessons.md`). `NoObjectGeneratedError.isInstance(err) && typeof err.text === "string"` → `json5.parse(err.text)`. To jest dodatkowa warstwa bezpieczeństwa oprócz structured output — agent ma podwójną szansę na poprawną odpowiedź.

### min-score extraction — language-neutral

```js
Math.min(...Object.values(scores))
```
Działa na **dowolnych** kluczach criterionów — min-score z 6 kluczy security-focused będzie działał tak samo jak z 6 kluczy code-quality. Nie trzeba modyfikować workflow shellscript.

---

## Co NOWE vs ZMIENIANE — pełna lista

### Pliki do STWORZENIA od zera (nowe)
```
.github/workflows/ai-security-review.yml          ← ~302 linie, kopia ai-code-review.yml z 5-7 zmianami
.github/actions/ai-security-reviewer/action.yml   ← ~90 linii, kopia ai-reviewer/action.yml z 3 zmianami
tools/ai-security-reviewer/package.json           ← kopia z name="ai-security-reviewer"
tools/ai-security-reviewer/package-lock.json      ← generowany przez npm install
tools/ai-security-reviewer/tsconfig.json          ← identyczny
tools/ai-security-reviewer/.nvmrc                 ← identyczny
tools/ai-security-reviewer/src/review.ts          ← identyczny (CLI glue)
tools/ai-security-reviewer/src/input.ts           ← identyczny (diff stripper + prompt builder)
tools/ai-security-reviewer/src/agent.ts           ← prawie identyczny (import schema zmienia się)
tools/ai-security-reviewer/src/schema.ts          ← NOWE kryteria security
tools/ai-security-reviewer/src/instructions.ts    ← NOWY prompt security-focused
tools/ai-security-reviewer/test/schema.test.ts    ← analogiczny do ai-code-reviewer
tools/ai-security-reviewer/test/agent.test.ts     ← analogiczny
tools/ai-security-reviewer/test/input.test.ts     ← identyczny lub bliski
tools/ai-security-reviewer/test/instructions.test.ts ← NOWY, asertuje security terminy
```

### Pliki do ZMIANY (istniejące)
Brak — ticket mówi o "separate workflow, not folded into existing". Żaden istniejący plik nie jest modyfikowany.

---

## Decyzje Otwarte dla /10x-plan

1. **Gate rule dla security**: czy zachować `verdict==pass AND min_score≥4` czy uprościć do `verdict==pass`?
   - Za `min_score≥4`: dodatkowe zabezpieczenie gdy model mówi "pass" ale jeden criterion ma 2/10
   - Za samym `verdict`: security review jest bardziej binarne; criteriony to analityczne, verdict jest syntetyczny
   - **Rekomendacja**: zachować `min_score≥4` — spójność z istniejącym gate'em, mniej wyjątkowych przypadków do dokumentacji

2. **Kryteria security (schema.ts)**: ticket wymienia: *hardcoded secrets, injection risks, missing input validation, unsafe dependency changes, auth/permission regressions*.
   Propozycja kluczy:
   ```
   secretsLeakage       ← hardcoded secrets / committed credentials
   injectionRisk        ← SQL/command/prompt injection
   inputValidation      ← missing validation at system boundaries
   dependencySafety     ← unsafe dependency changes (pinning, known vulns)
   authPermissions      ← auth/permission regressions
   transportSecurity    ← TLS, encryption, safe transport
   ```
   
3. **`node_modules` bootstrap**: nowy package `tools/ai-security-reviewer/` potrzebuje `npm install` przed pierwszym użyciem w CI (`npm ci` wymaga `package-lock.json` w repo). Plan musi uwzględnić commitowanie `package-lock.json`.

4. **Branch protection**: ticket mówi o "soft-launch period as non-blocking before promoting to required". Plan powinien to explicite zaadresować — dodać `ai-security-review/verdict` jako required status check to osobny krok (po stabilizacji), nie w tym samym PR.

---

## Open Questions

- Czy `/security-review` built-in skill (Claude Code) ma inne kryteria niż te z ticket description? Warto ją wywołać lokalnie na przykładowym diff jako kalibrację prompta dla `instructions.ts`.
- `.nvmrc` — skopiować identyczne czy sprawdzić aktualną wersję Node? (bezpieczniej: identyczne → `tools/ai-code-reviewer/.nvmrc`)
- Czy `GOOGLE_CLOUD_REGION: europe-central2` pozostaje? Per wcześniejszego memory: global endpoint jest preferowany dla Gemini (PUL-related fix). Sprawdzić czy workflow env powinien używać globalnego endpointu.
