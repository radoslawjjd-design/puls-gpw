# CLAUDE.md

puls-gpw: ESPI/EBI analyzer for GPW/NewConnect — Python 3.13, FastAPI, uv. See `@AGENTS.md` for agent-specific rules and project conventions.

## Project Rules

<!-- These rules duplicate content in the toolkit block below; the duplication is intentional — the toolkit block is managed by 10x-cli and must not be manually edited. -->

- Never write to `context/archive/` — that directory is immutable. Open new work with the appropriate skill instead.
- Secrets (Gemini API key, SMTP credentials, BigQuery service account) live in environment variables only. Never commit them.
- Destructive infra actions (drop a BigQuery table, delete a Cloud Run job, rotate a primary secret) are human-only — never automate them.

### Issue Tracking — close-out after /10x-implement epilogue

After the epilogue commit lands in `/10x-implement`, read `change.md` for the `tracking:` block. If present, execute ALL of the following that apply — then print a summary block to the console:

```
── Issue tracking close-out ──────────────────────
✓ Linear <id>  → Done
✓ GitHub #<n>  → closed
──────────────────────────────────────────────────
```

**Linear** (`tracking.linear`): call `mcp__linear-server__save_issue` with `state: Done` and append an implementation summary to the description (commits, delivered files).

**GitHub** (`tracking.github`): run `gh issue close <n> --repo <remote> --comment "..."` with the same commit SHAs.

If `tracking:` is absent or empty, skip silently — do not prompt the user.

### change.md — tracking field

Every `change.md` must include a `tracking:` block. When creating a new change with `/10x-new`, populate it immediately — ask the user for the Linear issue ID and GitHub issue number if not already known:

```yaml
tracking:
  linear: PUL-X   # Linear issue ID, e.g. PUL-6
  github: N        # GitHub issue number, e.g. 2
```

Leave values as `null` only if no corresponding issue exists.

<!-- BEGIN @przeprogramowani/10x-cli -->

## 10xDevs AI Toolkit - Module 3, Lesson 4 (E2E Tests)

**For E2E tests, use the `/10x-e2e` skill.** It is the single source of truth
for the workflow — risk → seed test + rules → generate → review against the five
anti-patterns → re-prompt → verify. The skill's `references/` carry the full
rules, anti-patterns, seed pattern, and prompt-template.

A few hard rules that hold even before you invoke the skill:

- **Locators:** `getByRole` / `getByLabel` / `getByText` first; `getByTestId`
  only when accessibility attributes are ambiguous. Never CSS selectors, XPath,
  or DOM structure.
- **Never `page.waitForTimeout()`.** Wait for state: `toBeVisible()`,
  `waitForURL()`, `waitForResponse()`.
- **Test independence + cleanup.** Each test runs standalone — its own setup,
  action, assertion, and cleanup; unique ids (timestamp suffix) so parallel runs
  and re-runs don't collide.

Two boundaries to keep straight:

- **DOM (snapshot) is the default.** Vision (`--caps=vision`) is a supplement for
  visual-only risks (layout, z-index, animation); for pixel regression prefer
  deterministic tools (`toMatchSnapshot`, Argos, Lost Pixel). VLM model
  selection/cost is a debugging topic (Lesson 5), not testing.
- **Healer helps on selectors, harms on logic.** A changed selector → healer
  re-finds it (route through PR review). A changed business behavior → healer
  masks the bug; that failing-test-to-fix case is Lesson 5.

<!-- END @przeprogramowani/10x-cli -->
