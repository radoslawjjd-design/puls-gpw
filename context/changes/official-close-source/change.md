---
change_id: official-close-source
title: Use the official GPW close for kurs_zamkniecia instead of the bankier listing figure
status: implemented
created: 2026-07-27
updated: 2026-07-28
archived_at: null
tracking:
  linear: PUL-98
  github: 193
---

## Notes

switch company_daily_stats.kurs_zamkniecia source from bankier listing to gpw.pl GPWQuotations AJAX table (PUL-98 / GH #193)

Ticket context (Linear PUL-98, verified 2026-07-25):

- `company_daily_stats.kurs_zamkniecia` differs from the official GPW close by a small, randomly signed
  amount (0.1–0.4%). Only 1 of 24 sampled (ticker, date) pairs matched; GPW and stooq agreed 12/12.
- Root cause is **not** scheduler timing — `fetched_at` shows the final daily write at 15:31 UTC
  (17:31 Europe/Warsaw), ~26 min after the fixing. The cause is the **source**: the bankier.pl listing
  page does not publish the official close (KRU's stored value = GPW best bid, PKO's = best ask).
- Verified replacement source:
  `https://www.gpw.pl/ajaxindex.php?action=GPWQuotations&start=showTable&tab=all&lang=PL&type=&full=1&format=html`
  — HTTP 200, no auth, 372 rows, plain HTML table (BeautifulSoup, like `fetch_etf_page`).
  Column 12 `Kurs ost. trans. / zamk.` = official close (12/12 vs stooq); column 7 `Kurs odn.` =
  previous close (free self-check). Also carries otw./min./maks./wolumen/obrót/liczba transakcji/ISIN/Skrót.
  Outside session hours serves the last completed session.
- Open question: NewConnect not covered (372 main-market names vs 744 instruments). `newconnect.pl/notowania`
  uses `action=NCExternalDataFrontController` — needs a short spike, or NewConnect is explicitly scoped out.
- Historical rows: decide backfill-correct or not. PUL-92 rows (2011–2026) already carry stooq closes,
  so the discrepancy is confined to scraper-written rows from 2026-06-26 on.
- Related: PUL-92 (backfill), PUL-96 / GH #191 (backfilled closes are dividend-adjusted).

References: `company_stats_main.py`, `src/bankier_metrics.py:fetch_listing_page`,
parser to mirror: `src/gpw_etf_metrics.py:fetch_etf_page`.

## Decisions (user, 2026-07-27, after research)

1. **Source priority — GPW/NC always wins.** The daily job reads the GPW main-market table and the
   NewConnect table. Bankier stays **only as a gap-filler** for tickers present in `companies` but
   absent from both feeds (~47 today, ≥18 actively priced). A bankier value must **never** overwrite
   a GPW/NC value for the same ticker — the merge happens in the job before the write, with the
   official feeds taking precedence.
2. **Scope — forward fix + a 13-month corrective pass** (~186 000 rows, trailing ~270 sessions)
   sourced from `gpw.pl/archiwum-notowan`. Not the full 2011–2026 span: nothing renders beyond
   `range=1y`, so ~90% of the 1.9 M backfilled rows are invisible, while the `Nazwa → Skrót`
   mapping degrades with age (90% today → 69% at 5 years). The correction therefore closes GH #193
   entirely and GH #191 (dividend-adjusted closes) across the whole visible surface.
   **The corrective pass is GPW main market only** — `archiwum-notowan?type=10` covers 372
   main-market names and no per-date NewConnect archive endpoint was found. Verified this is
   harmless: all 11 stock holdings across all portfolios sit on the main market, zero on NewConnect
   (the other 3 held instruments are ETFs in `etf_quotes`). NewConnect still gets the forward fix
   from the live feed; only its *history* stays as PUL-92 left it.
3. **Correction mapping is hard-gated.** Only exact, unambiguous `Nazwa → Skrót` hits are written;
   anything else is logged and skipped. No fuzzy matching — a mis-mapped name writes a
   plausible-looking price against the wrong ticker, which is the failure class this change exists
   to remove.
4. **No scheduler change.** The close settles in the live feed at ~17:15 Warsaw and never moves; the
   existing `1,31 9-17 * * 1-5` cron's 17:31 tick has ~16 minutes of margin. No human-only infra
   step is required.
