# Vertex AI costs in the billing export (PUL-69)

Run 2026-08-06 against `puls-gpw.espi_ebi.gcp_billing_export_v1_01E214_63C8A3_9E57E3`.
Export covers **2026-07-16 → 2026-08-05**; currency is **PLN**.

## The checklist, answered

| Ticket item | Result |
|---|---|
| Does the export table exist? | **Yes.** `bq show` returns 0. Day-partitioned. |
| Vertex AI cost by region | **12.07 PLN gross**, spread over 15 regions |
| Still zero after 48h? | **No** — costs were never zero, only invisible before the table existed |

## Correction: the ticket expected the wrong region

The ticket read Cloud Monitoring as "3164 requests to europe-west4" and expected the cost to
land there. It does not. `location=global` means what it says:

| Region | Gross PLN | Rows |
|---|---:|---:|
| europe-central2 | 6.1036 | 252 |
| europe-north1 | 1.7749 | 218 |
| **europe-west4** | 1.0427 | 274 |
| us-central1 | 0.6893 | 588 |
| us-east4 | 0.4961 | 164 |
| europe-west11 | 0.3705 | 18 |
| us-south1 | 0.3613 | 17 |
| asia-northeast1 | 0.2847 | 287 |
| asia-east1 | 0.2825 | 373 |
| us-west4 | 0.2507 | 140 |
| us-east7 | 0.1722 | 121 |
| northamerica-northeast1 | 0.1473 | 174 |
| europe-west1 | 0.0748 | 120 |
| us-east1 | 0.0133 | 18 |
| us-west5 | 0.0098 | 28 |

`europe-west4` is third at 9% of spend. The largest single region is `europe-central2` — the
one we supposedly moved *away* from — at 51%. The global endpoint places each request
wherever there is capacity, so a monitoring view filtered to one region shows a slice, not
the destination. **Nothing routes anywhere in particular, and that is the point:** it is
precisely why the global endpoint does not hit the per-region dynamic quota that produced
the 429s.

## Net cost is zero, and why that is not the same as free

Every gross złoty is offset by one promotional credit:

| Month | Gross | Credit | Net |
|---|---:|---:|---:|
| 2026-07 | 9.2552 | −9.2561 | −0.0009 |
| 2026-08 (to 08-05) | 2.8182 | −2.8184 | −0.0001 |

Credit name `FreeTrialUpgrade:CreditId-FreeTrial:Credit-01E214-63C8A3-9E57E3`, type
`PROMOTION`. It covers the whole project, not just Vertex:

| Service | Gross PLN | Net PLN |
|---|---:|---:|
| Cloud Run | 15.8203 | −0.0008 |
| Vertex AI | 12.0734 | −0.0011 |
| Secret Manager | 2.4132 | −0.0012 |
| Artifact Registry | 2.2874 | −0.0011 |
| Cloud Scheduler | 0.9566 | −0.0001 |
| everything else | ~0.006 | ~0.006 |

So the honest reading is **not** "Vertex AI is free". It is: the project bills ~33.6 PLN
gross over three weeks and a trial credit is currently paying all of it. **When that credit
is exhausted or expires, the invoice becomes real** — on this run rate, on the order of
**45–50 PLN/month all-in, of which ~17 PLN is Vertex AI.**

The billing export does not carry the credit's remaining balance or expiry date; that lives
in Billing → Credits in the console and needs a human to look. That is the one item this
investigation could not close.

## What the money buys

| SKU | Gross PLN | Units |
|---|---:|---:|
| Gemini 2.5 Flash GA Text Input | 5.7198 | 5,062,475 |
| Gemini 2.5 Flash GA Thinking Text Output | 3.9001 | 414,506 |
| Gemini 2.5 Flash Lite Text Input | 1.6134 | 4,289,554 |
| Gemini 2.5 Flash GA Text Output (Thinking On) | 0.5607 | 59,605 |
| Gemini 2.5 Flash Lite Text Output | 0.2737 | 182,120 |
| Flash GA / Lite input caching | 0.0058 | 83,478 |

The export labels the unit `requests`. At 5 M in three weeks that label cannot be literal —
these are token counts, and the naming is Google's, not ours. Worth knowing before anyone
reads "requests" as calls and concludes something alarming.

## One spike worth naming

Daily gross runs ~0.2–0.3 PLN. **2026-08-05 was 2.02 PLN**, driven by Flash GA input tokens
going 154,724 → 1,065,040 in a day (6.9×) with output rising in step.

Input and output moved together, which is the shape of more or larger documents rather than
a retry loop — a retry storm inflates input while output stays flat. Not diagnosed further;
recorded because a second such day would be the signal to look at what the scraper fed the
model, and one data point is not a trend.

## Queries

```sql
-- Cost by region
SELECT IFNULL(location.region,'(none)') AS region, ROUND(SUM(cost),4) AS gross, COUNT(*) AS n
FROM `puls-gpw.espi_ebi.gcp_billing_export_v1_01E214_63C8A3_9E57E3`
WHERE service.description LIKE '%Vertex%'
GROUP BY 1 ORDER BY 2 DESC;

-- Gross vs credit, by month
SELECT FORMAT_DATE('%Y-%m', DATE(usage_start_time)) AS month,
       IFNULL(c.name,'(no credit)') AS credit_name, IFNULL(c.type,'') AS credit_type,
       ROUND(SUM(cost),4) AS gross, ROUND(SUM(IFNULL(c.amount,0)),4) AS credit,
       ROUND(SUM(cost)+SUM(IFNULL(c.amount,0)),4) AS net
FROM `puls-gpw.espi_ebi.gcp_billing_export_v1_01E214_63C8A3_9E57E3`
LEFT JOIN UNNEST(credits) AS c
WHERE service.description LIKE '%Vertex%'
GROUP BY 1,2,3 ORDER BY 1;

-- Whole project, so Vertex is read in proportion
SELECT service.description AS service, ROUND(SUM(cost),4) AS gross,
       ROUND(SUM(cost)+SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c),0)),4) AS net
FROM `puls-gpw.espi_ebi.gcp_billing_export_v1_01E214_63C8A3_9E57E3`
GROUP BY 1 ORDER BY 2 DESC;
```

The ticket's original query used `location.region` with an alias of `cost_pln` and a
`usage_start_time >= '2026-07-01'` bound. The currency really is PLN, and the bound is
harmless — the export simply starts on 2026-07-16.

## Conclusion

Close PUL-69. Nothing to fix, and specifically **do not** revert to the `europe-central2`
regional endpoint: it is already the largest destination the global endpoint picks, and
pinning to it is what caused the 429s.

Two things worth carrying forward, neither of them this ticket's job:

1. **Check the trial credit's balance and expiry in the console** — the only question this
   investigation could not answer from data, and the one that decides whether ~50 PLN/month
   starts landing on an invoice.
2. **Artifact Registry at 2.29 PLN is partly self-inflicted** — every no-op deploy pushed
   another image. PUL-122 stops the documentation-only ones.
