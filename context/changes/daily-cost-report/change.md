---
change_id: daily-cost-report
title: Daily cost report emailed at 09:00 with anomaly flagging
status: preparing
created: 2026-08-06
updated: 2026-08-06
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

Follows the established infra pattern — job defined in `deploy.yml` alongside the four existing ones, Cloud Scheduler entry
modelled on `puls-gpw-etf-quotes-trigger` (created once, by hand; scheduler creation is not part of CI).
