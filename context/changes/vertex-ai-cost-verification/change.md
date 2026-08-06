---
change_id: vertex-ai-cost-verification
title: Verify Vertex AI costs in the BigQuery billing export
status: implemented
created: 2026-08-06
updated: 2026-08-06
archived_at: null
tracking:
  linear: PUL-69
  github: null
---

## Notes

An investigation, not a code change. The ticket's checklist was written on 2026-07-16, when
the billing export table did not exist yet and the working theory was that the `global`
Vertex endpoint routed to `europe-west4`.

Both premises turned out to be wrong in the ticket's favour: the table exists, the costs are
there, and they are spread across fifteen regions on three continents. Findings and the
queries that produced them are in `findings.md`.

No code change. The one thing the ticket floated as a remedy — reverting to the
`europe-central2` regional endpoint — is off the table: that endpoint is what produced the
429s (see the dynamic-quota note in `context/foundation/lessons.md`), and there is nothing
to remedy anyway.
