---
name: portfolio-xpost
description: Generate and publish portfolio status X threads from XTB broker screenshots in broker_data/ (main/IKZE wallets)
allowed-tools:
  - Read
  - Glob
  - Bash
  - AskUserQuestion
---

# /portfolio-xpost — Portfolio status X-post generator

Reads XTB broker screenshots from `broker_data/<wallet>/` (`main`, `ikze`),
extracts portfolio data via Gemini vision, computes day-over-day deltas from the
`portfolio_snapshots` BigQuery table, drafts one X thread (main+IKZE),
gets human approval, publishes with the screenshots attached, and archives the processed
images. See `context/changes/portfolio-xpost-skill/plan.md` for the full design.

## Initial Response

When this skill is invoked, immediately start the Process below — no argument is required.
If `broker_data/` does not exist at all, tell the user it's missing and stop (per the plan,
this skill never auto-creates `broker_data/<wallet>/` subfolders).

## Process

### Step 1 — Discover screenshots and extract portfolio data

For each wallet in `main`, `ikze`:

1. **List screenshots**: `Glob` `broker_data/<wallet>/*` (any image extension).

> **Path safety**: the snippets below interpolate wallet names and screenshot paths
> directly into a `python -c "..."` string. Wallet names are a fixed enum
> (`main`/`ikze`), but screenshot filenames come from the user's
> filesystem. Before interpolating a path, confirm it contains no `"` or `\`
> characters; if one does, rename the file (or copy it to a safe temp path) before
> proceeding rather than interpolating it as-is.

2. **Empty subfolder — check for an already-published-today row** before halting. Run:

   ```bash
   python -c "
   from datetime import date, timedelta
   from dotenv import load_dotenv
   load_dotenv()
   from db.bigquery import get_latest_snapshot_before
   tomorrow = date.today() + timedelta(days=1)
   row = get_latest_snapshot_before('<wallet>', tomorrow)
   print(row['snapshot_date'] == date.today() if row else False)
   "
   ```

   - Prints `True` → a snapshot for today already exists for this wallet (e.g. its thread
     published successfully on a prior run and its screenshots were already archived).
     Skip this wallet for the rest of the run — do not halt, do not re-extract.
   - Prints `False` → genuinely missing data. HALT the whole run with a clear error naming
     the empty wallet folder; do not generate a partial thread. Tell the user which
     `broker_data/<wallet>/` folder needs a screenshot before retrying.

3. **Non-empty subfolder — extract via Gemini vision**:

   ```bash
   python -c "
   from dotenv import load_dotenv
   load_dotenv()
   import json
   from src.gemini_client import extract_portfolio_snapshot
   result = extract_portfolio_snapshot(['<path1>', '<path2>', ...])
   print(json.dumps({
       'total_value': result.total_value,
       'currency': result.currency,
       'positions': [{'ticker': p.ticker, 'value': p.value, 'pct': p.pct} for p in result.positions],
       'uncertain_fields': result.uncertain_fields,
   }))
   "
   ```

   Parse the printed JSON. Keep the per-wallet result in memory for Step 2. If the command
   raises (`AnalysisError`), treat it like a HALT case — the model could not parse a usable
   response — and surface the raw error to the user instead of guessing values.

4. **Surface uncertainty before continuing.** After extracting every non-skipped wallet,
   collect any wallet whose `uncertain_fields` is non-empty. If at least one exists, present
   the wallets and their uncertain fields, then ask:

   - question: "Extraction flagged uncertain fields. How should I proceed?"
     header: "Uncertain data"
     options:
     - label: "Continue anyway"
       description: "Use the extracted values as-is — review the numbers in the draft before approving."
     - label: "Stop — I'll fix the screenshot(s)"
       description: "Halt now; replace the flagged wallet's screenshot(s) in broker_data/<wallet>/ and re-run."
     multiSelect: false

   On "Stop": HALT the run, name the affected wallet(s)/fields, nothing else happens.
   On "Continue anyway": proceed to Step 2 with the extracted values as given.

### Step 2 — Compute deltas and draft the two threads

1. **Day-over-day delta per wallet** (skip wallets already handled as "skip — published
   today" in Step 1):

   ```bash
   python -c "
   from datetime import date
   from dotenv import load_dotenv
   load_dotenv()
   from db.bigquery import get_latest_snapshot_before
   row = get_latest_snapshot_before('<wallet>', date.today())
   print(row)
   "
   ```

   - `row is None` → first-ever run for this wallet; `day_change_abs`/`day_change_pct` are
     `None` (no prior data to diff against) — do not invent a delta.
   - `row` present → `day_change_abs = total_value - row['total_value']`;
     `day_change_pct = day_change_abs / row['total_value'] * 100` (guard divide-by-zero:
     `None` if `row['total_value'] == 0`).

2. **Draft the thread.** The thread covers `main` + `ikze`. It is a 2-tweet reply chain —
   tweet 1 is a combined header (both wallets), tweet 2 is the combined "Liderzy portfela"
   leaders tweet (per the real legacy format, corrected in plan.md's Phase 3 addendum after
   the original one-tweet-per-wallet draft was found not to match it). Skip a wallet if it
   was marked "already published today" in Step 1; if **both** wallets are skipped, skip
   the thread drafting entirely (nothing to approve/publish for this run).

   Compose via the deterministic composer (no Gemini call — the data is already extracted
   and delta-computed in Step 1; an LLM call here would only add paraphrase/rounding risk):

   ```bash
   python -c "
   from datetime import date
   from src.gemini_client import PortfolioPosition
   from src.portfolio_thread_composer import WalletThreadData, compose_portfolio_thread
   wallets = [
       WalletThreadData(
           wallet='<wallet>', total_value=<total_value>, currency='<currency>',
           total_profit_abs=<total_profit_abs>,
           positions=[PortfolioPosition(ticker=p['ticker'], value=p['value'], pct=p['pct'], profit_abs=p['profit_abs']) for p in <positions>],
           day_change_abs=<day_change_abs_or_None>, day_change_pct=<day_change_pct_or_None>,
       ),
       # ... one WalletThreadData per surviving wallet in this thread
   ]
   header, leaders = compose_portfolio_thread(date.today(), wallets)
   print(header)
   print('---')
   print(leaders)
   "
   ```

   Then validate both tweets with the reused ESPI/EBI discipline (`src/post_supervisor.py`),
   applied per-tweet — the header tweet (≤280 chars, no disclaimer required) and the leaders
   tweet (≤450 chars, X Premium-light, disclaimer required):
   - **Length**: header ≤ 280 chars, leaders ≤ 450 chars. If over, trim the least-important
     leader line first (lowest `profit_abs`) rather than truncating mid-line.
   - **No truncation**: never end a tweet with `...` or `…`.
   - **No investment-advice phrasing**: `from src.post_supervisor import _ADVICE_RE` and check
     `_ADVICE_RE.search(tweet)` is `None` for both tweets; if it matches, rewrite the
     offending phrase (`_INVESTMENT_ADVICE_PATTERNS` in that module shows what's banned).
   - **Disclaimer present**: only the **leaders** (closing) tweet must contain `rekomendacj`
     — the header tweet does not carry the disclaimer (mirrors the ESPI/EBI hook/closing
     split, where only the closing tweet carries it).
   - **No `$cashtag` in the leaders tweet**: tickers there are plain (`XTB`, not `$XTB`) —
     X's max-1-cashtag-per-tweet/403 limit applies per tweet, and the leaders tweet lists
     multiple tickers in one tweet (see `[[reference-cashtag-per-tweet]]`).
   - **No domain-like text**: `from src.post_generator import _strip_domain_suffix` and apply
     it to both the header and leaders tweets before presenting them — defuses any
     domain-like substring (e.g. a company name ending in `.pl`/`.com`/etc.) so X's
     client-side link detection never auto-renders it as a clickable link.

   Present the draft to the user as plain text before moving on to Step 3.

   **Known gaps, deferred** (not blocking, but real and worth knowing about): the
   "Doładowanie" deposit-narrative line (detecting new buys by diffing today's positions
   against the prior snapshot) is not implemented — it's simply omitted every run for now.
   The `sum(positions) + free_cash == total_value` cross-check from the original ticket is
   not implemented either. Both are candidates for a future follow-up, not this skill's v1.

### Step 3 — Approval gate (per thread)

Process the thread (from Step 2.2) if it survived.

For each thread:

1. Ask:

   - question: "Zatwierdzasz ten wątek do publikacji?"
     header: "Zatwierdzenie wątku"
     options:
     - label: "Zatwierdź"
       description: "Opublikuj ten wątek tak jak jest."
     - label: "Edytuj"
       description: "Podaj uwagę tekstową — przerobię wątek i pokażę ponownie do zatwierdzenia."
     - label: "Anuluj"
       description: "Nie publikuj. Zrzuty ekranu tego wątku zostają w broker_data/, nic nie trafia do BigQuery."
     multiSelect: false

2. **Zatwierdź** → mark the thread `APPROVED`, move to Step 4 for this thread.

3. **Edytuj** → ask the user for the free-text refinement (e.g. "skróć opis pozycji w IKZE",
   "popraw walutę"). Apply the requested edit directly to the affected tweet(s) — you (the
   orchestrating agent) revise the text yourself, there is no separate Gemini call for this
   (per Phase 3's addendum: thread text is template-composed, not LLM-generated). Re-run the
   same Step 2.2 validation checks (length ≤280, no truncation, `_ADVICE_RE`, disclaimer
   present, `_strip_domain_suffix` for domain-like text) on the revised text. Present the
   revised thread and return to step 1 of this gate
   for the same thread — loop until Zatwierdź or Anuluj.

4. **Anuluj** → mark the thread `CANCELLED`. Skip Step 4 and Step 5 entirely for this thread —
   its wallets' screenshots stay untouched in `broker_data/<wallet>/`, nothing is published or
   persisted. Tell the user this thread was cancelled and its screenshots remain in place for
   a future run.

### Step 4 — Publish

**Credentials.** `get_x_publisher()` reads `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
`X_ACCESS_SECRET` from the process environment (`src/x_publisher.py` `_CRED_VARS`). Locally
these are usually **not** in `.env` — they live in GCP Secret Manager on the `puls-gpw`
project as `x-api-key`, `x-api-secret`, `x-access-token`, `x-access-secret` (same secrets
Cloud Run injects via `puls-gpw-runner@`'s `secretmanager.secretAccessor`, per
`context/foundation/infra.md`). If the publish call fails with `Missing X API credentials`,
fetch them into the *current shell session only* — do not write them to `.env` or any file —
and retry the publish in the same Bash invocation (env vars set via `export` don't persist
across separate Bash tool calls):

```bash
export X_API_KEY=$(gcloud secrets versions access latest --secret=x-api-key --project=puls-gpw)
export X_API_SECRET=$(gcloud secrets versions access latest --secret=x-api-secret --project=puls-gpw)
export X_ACCESS_TOKEN=$(gcloud secrets versions access latest --secret=x-access-token --project=puls-gpw)
export X_ACCESS_SECRET=$(gcloud secrets versions access latest --secret=x-access-secret --project=puls-gpw)
# then run the publish python -c "..." command in the same Bash call
```

For each thread marked `APPROVED` in Step 3 (skip `CANCELLED` threads entirely):

1. Build `media_paths` — one entry per tweet, same order as the thread's tweets, where each
   entry is the **full list** of screenshot paths discovered for the corresponding wallet in
   Step 1.1 (not just the first one) — tweet 1 (header) gets all of the first wallet's
   screenshots, tweet 2 (leaders) gets all of the second wallet's screenshots. A wallet always
   has at least one screenshot here, since empty-and-skipped wallets never reach thread
   drafting. X allows at most 4 images per tweet; `publish_thread_with_media` caps and warns
   if more are passed.

2. Publish:

   ```bash
   python -c "
   from dotenv import load_dotenv
   load_dotenv()
   import json
   from src.x_publisher import get_x_publisher
   from src.exceptions import XPublisherError, XPublishPartialError
   tweets = [<tweet1>, <tweet2>]
   media_paths = [['<wallet1_screenshot1>', '<wallet1_screenshot2>', ...], ['<wallet2_screenshot1>', ...]]
   try:
       result = get_x_publisher().publish_thread_with_media(tweets, media_paths)
       print(json.dumps({
           'status': 'published',
           'tweet_ids': result.tweet_ids,
           'media_attached': result.media_attached,
       }))
   except XPublishPartialError as exc:
       print(json.dumps({'status': 'partial', 'published_ids': exc.published_ids, 'error': str(exc)}))
   except XPublisherError as exc:
       print(json.dumps({'status': 'failed', 'error': str(exc)}))
   "
   ```

3. Parse the printed JSON:
   - `status == "published"` → proceed to Step 5 for this thread's wallets. A tweet with
     `media_attached[i] == False` still counts as published (text-only fallback) — continue,
     but carry that flag into Step 5's persistence so the degraded publish is visible in BQ.
   - `status == "partial"` or `"failed"` → this is a text-publish failure (per the Critical
     Implementation Details "Media upload fallback ordering" — a media failure alone never
     reaches this branch, only a `create_tweet` failure does). **Skip Step 5 entirely for this
     thread.** Its wallets' screenshots remain untouched in `broker_data/<wallet>/` for the
     next run to retry (Step 1's already-published-today check will correctly skip only the
     wallets that *did* make it onto X, if any — but since nothing in `portfolio_snapshots` is
     written for a failed thread, every wallet in it is retried in full next run). Tell the
     user this thread failed and that its screenshots are intact for a retry.

### Step 5 — Persist and archive

For each wallet in a thread that published successfully in Step 4, in this order (per wallet,
do persist immediately followed by archive — do not batch across wallets, so a failure on one
wallet's archive doesn't leave another wallet's snapshot row without its own archive):

1. **Persist** — build `positions_json` as a JSON object (not a bare list) so the
   media-attachment outcome is queryable alongside the positions:

   ```bash
   python -c "
   from datetime import date
   from dotenv import load_dotenv
   load_dotenv()
   import json
   from db.bigquery import save_portfolio_snapshot
   positions_json = json.dumps({
       'positions': [{'ticker': p['ticker'], 'value': p['value'], 'pct': p['pct']} for p in <positions>],
       'media_attached': <media_attached_bool_for_this_wallet>,
   })
   snapshot_id = save_portfolio_snapshot(
       wallet='<wallet>',
       snapshot_date=date.today(),
       total_value=<total_value>,
       currency='<currency>',
       day_change_abs=<day_change_abs_or_None>,
       day_change_pct=<day_change_pct_or_None>,
       positions_json=positions_json,
   )
   print(snapshot_id)
   "
   ```

2. **Archive** — move every screenshot file discovered for this wallet in Step 1.1 (there may
   be more than one) from `broker_data/<wallet>/` to `broker_data/archive/<YYYY-MM-DD>/<wallet>/`
   (create the destination directory if needed; `<YYYY-MM-DD>` is today's date):

   ```bash
   mkdir -p "broker_data/archive/<YYYY-MM-DD>/<wallet>"
   mv "broker_data/<wallet>/<screenshot>" "broker_data/archive/<YYYY-MM-DD>/<wallet>/"
   ```

   Repeat the `mv` for each screenshot file belonging to this wallet.

After all `APPROVED` threads have run through Step 4 and Step 5, summarize the run for the
user: which threads published (with tweet ids), which wallets were persisted/archived, which
threads were cancelled or failed and why, and which wallets (if any) still need a retry.

## What this skill does NOT do (yet / ever)

- Does not auto-create `broker_data/<wallet>/` subfolders or validate screenshot format
  beyond "image file present" — the user places the screenshots.
- Does not modify the existing ESPI/EBI pipeline (`post_selection.py`, `post_generator.py`,
  `post_supervisor.py`, `post_main.py`) or its `x_posts` table — only reuses its validation
  conventions.
- Does not delete screenshots — they are archived under `broker_data/archive/<date>/<wallet>/`,
  never removed from disk.
- Does not run on a schedule — user-invoked only, like every other skill in this repo.
