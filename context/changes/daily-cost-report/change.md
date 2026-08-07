---
change_id: daily-cost-report
title: Daily cost report emailed at 09:00 with anomaly flagging
status: impl_reviewed
created: 2026-08-06
updated: 2026-08-07
archived_at: null
tracking:
  linear: PUL-125
  github: 256
---

## Notes

PUL-125 / GitHub #256: Cloud Run Job `puls-gpw-cost-report`, daily 09:00 Europe/Warsaw, queries the BigQuery billing export
(`puls-gpw.espi_ebi.gcp_billing_export_v1_01E214_63C8A3_9E57E3`) and emails a per-service cost summary via the existing SMTP
path in `src/notifier.py`.

Decisions already taken with the owner (recorded on PUL-125, not up for re-litigation during planning):

- **Window** — D-1 marked provisional, plus month-to-date. Billing rows keep arriving and being amended for 1-2 days, so a
  bare D-1 figure reads low and never corrects itself; the MTD total re-settles in the next morning's mail.
- **Cadence** — daily, not threshold-only. Silence must not be ambiguous between "nothing unusual" and "the job died"; the
  daily mail proves liveness and the subject line carries the anomaly signal.
- **Content** — per service, gross and net of credits, plus Vertex AI broken down by model (`gemini-2.5-flash` /
  `gemini-2.5-flash-lite`) with token counts, since that is the axis a spike actually moves along.
- **Anomaly rule** — day total vs trailing 7-day median, breach by a configured factor flips the subject.

Constraint from PUL-69: the trial credit's remaining balance and expiry are **not** in the billing export. The report can show
credit consumed, never credit left.

## Decisions taken after research (2026-08-06)

Settled with the owner once `research.md` had quantified the trade-offs. Not open for
re-litigation during planning.

- **Anomaly factor — `2.0`**, default of `COST_ANOMALY_FACTOR`. On the 21 days of export it fires
  twice, including the 2026-08-05 Flash-GA spike this ticket exists to catch. `2.5` would have
  fired never in the same window, which makes the flag decorative.
- **Anomaly runs on gross, never net.** Net is `−0.0001 PLN` every day; the trial credit offsets
  everything. Both figures are still reported.
- **Median in Python, not SQL.** SQL returns 8 daily totals; `statistics.median` does the rest.
  Exact, and the decision logic ends up where unit tests actually run it — `PERCENTILE_CONT`
  cannot take a window frame in BigQuery anyway.
- **Mail is HTML in the Faro chrome**, modelled on `_announcement_digest_html`.
- **`infra.md` gets both missing jobs.** `puls-gpw-etf-quotes` and its trigger were never added
  and the prose still says "wszystkie trzy joby"; this change fixes that alongside adding
  `puls-gpw-cost-report`.

Follows the established infra pattern — job defined in `deploy.yml` alongside the four existing ones, Cloud Scheduler entry
modelled on `puls-gpw-etf-quotes-trigger` (created once, by hand; scheduler creation is not part of CI).
