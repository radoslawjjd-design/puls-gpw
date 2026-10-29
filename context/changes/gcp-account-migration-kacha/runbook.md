# Migration execution record

Source: `puls-gpw`; destination: `puls-gpw-kp-20261029`; region: `europe-central2`.
Account identities and billing IDs are in the local ignored scope file.

## Progress

- [x] Target administrator authenticated without switching default CLI account.
- [x] Dedicated migration branch and change record created.
- [x] Source inventory captured without secrets/row data; temporary query datasets excluded.
- [x] Destination project created and linked to the target's existing billing account.
- [x] Required initial destination APIs enabled.
- [x] Auth/data and deployment research completed by two read-only agents.
- [x] Target service accounts, IAM, repository, dataset and Firebase created.
- [x] Secrets transferred in memory; target Firebase credentials configured.
- [x] Initial BigQuery and Firebase copies verified (15 tables, 7 accounts).
- [x] Images/jobs/private API deployed; target schedules remain inactive.
- [x] Portable billing/CI configuration implemented; 996 tests pass, 9 skipped.
- [ ] API/auth/Vertex/Bankier probes verified without publishing or email.
- [ ] Source writes frozen and in-flight work drained.
- [ ] Final data/identity synchronization and comparisons pass.
- [ ] Public access and intended schedules switched to destination.
- [ ] CI and monitoring switched; target billing export configured.
- [ ] Rollback record and final target checks complete.

## Constraints

Private API probes passed: health, frontend, admin authorization, announcement
query, ticker autocomplete. Target Firebase credential reads all 7 preserved UIDs.
Cloud Run BQ probe passed. Bankier still returns 403 with a Cloudflare challenge.
Legacy Gemini 2.5 model returned 404 in the new project; supported successors
are being probed before cutover. Target billing export enabled by the user.

Do not delete source resources, rotate primary source secrets, or write archives
or foundation documents. Keep source schedules and API intact until the final
freeze. Never have two active pipeline copies. No credential values, password
hashes, user records or unredacted exports in tracked files or tool output.
Keep source billing export as historical data and preserve account-wide cost
report semantics. The new Cloud owner is not a request to change SMTP sender.

## Rollback

Before target writes: source inventory records original scheduler states and API
ingress; restore those and keep destination schedulers inactive. Source image and
credentials remain available. After target writes: stop target writers, reconcile
target BQ/Firebase changes into source, verify, then restore source. Never blindly
resume stale source data.
