-- PUL-102 — data repair. RUN BY A HUMAN, NOT BY AN AGENT.
--
-- Destructive operations on production are human-only (project rule). Nothing in
-- this file has been executed. Run the statements top to bottom; the order is
-- load-bearing and each step says why.
--
-- Project: puls-gpw   Dataset: espi_ebi
-- Prepared: 2026-08-04, against the state verified that day.
--
-- The statements are written to be robust to drift: no row count and no snapshot
-- date is hardcoded, because the pipeline keeps writing until the code fix in
-- this change ships. Re-read STEP 0 before you start.


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 0 — Look before you touch. Expect: Żabka, przejęty, and the two -PDA rows.
-- ─────────────────────────────────────────────────────────────────────────────
SELECT ticker, name, isin, hop_url
FROM `puls-gpw.espi_ebi.companies`
WHERE NOT REGEXP_CONTAINS(ticker, r"^[A-Z0-9][A-Z0-9-]{0,15}$")
ORDER BY ticker;

SELECT ticker, COUNT(*) AS price_rows, MIN(snapshot_date) AS first_date, MAX(snapshot_date) AS last_date
FROM `puls-gpw.espi_ebi.company_daily_stats`
WHERE ticker IN ("ZAB", "Żabka", "przejęty")
GROUP BY ticker ORDER BY ticker;


-- ─────────────────────────────────────────────────────────────────────────────
-- ŻABKA — one company that became two identities
-- ─────────────────────────────────────────────────────────────────────────────

-- STEP 1 — Move isin and hop_url onto ZAB BEFORE deleting Żabka.
-- The good-looking row is the incomplete one: ZAB has both fields NULL, and the
-- populated ones live on the broken row. Deleting first loses them.
UPDATE `puls-gpw.espi_ebi.companies` T
SET isin = S.isin, hop_url = S.hop_url, updated_at = CURRENT_TIMESTAMP()
FROM (SELECT isin, hop_url FROM `puls-gpw.espi_ebi.companies` WHERE ticker = "Żabka") S
WHERE T.ticker = "ZAB";

-- STEP 2 — Re-key the price rows ZAB does not already have.
-- These dates are real trading sessions (732 other tickers have rows for
-- 2026-07-27 and 2026-07-28), and ZAB is simply missing them: the official feed
-- keys on the GPW abbreviation, so it wrote nothing, while the gap-filler wrote
-- under the brand name. These rows are the only prices we hold for those days.
--
-- Matched dynamically rather than by hardcoded dates, so this stays correct
-- however many rows have accrued by the time you run it.
--
-- Their provenance is the bankier gap-filler, not the official close — `source`
-- is left as it is so that stays visible, and a later archive correction
-- (the PUL-98 path) will overwrite them with the official figure.
UPDATE `puls-gpw.espi_ebi.company_daily_stats` T
SET ticker = "ZAB"
WHERE T.ticker = "Żabka"
  AND NOT EXISTS (
    SELECT 1 FROM `puls-gpw.espi_ebi.company_daily_stats` A
    WHERE A.ticker = "ZAB" AND A.snapshot_date = T.snapshot_date
  );

-- STEP 3 — Delete what is left, which is now only duplicates of ZAB rows.
DELETE FROM `puls-gpw.espi_ebi.company_daily_stats`
WHERE ticker = "Żabka";

-- STEP 4 — Re-point the announcements. Four rows as of 2026-08-04.
UPDATE `puls-gpw.espi_ebi.announcements`
SET ticker = "ZAB"
WHERE ticker = "Żabka";

-- STEP 5 — Now the identity row can go.
DELETE FROM `puls-gpw.espi_ebi.companies`
WHERE ticker = "Żabka";


-- ─────────────────────────────────────────────────────────────────────────────
-- MEGAPIXEL — delete, per the owner's decision of 2026-08-04
--
-- Not renamed to MPS. The price has been frozen since 2026-07-28 — 1.70 PLN,
-- turnover 840.55, 11 transactions, identical every single day — and the heading
-- literally reads "przejęty". The instrument is off the market, so an MPS row
-- would be a live-looking identity for a dead company that keeps accruing frozen
-- rows from the gap-filler. No announcement references it, so nothing is orphaned.
--
-- If it turns out to still trade, STEP 6/7 are the wrong call — check STEP 0's
-- output first, and rename instead:
--   UPDATE `puls-gpw.espi_ebi.companies` SET ticker="MPS" WHERE ticker="przejęty";
-- ─────────────────────────────────────────────────────────────────────────────

-- STEP 6 — the frozen price rows.
DELETE FROM `puls-gpw.espi_ebi.company_daily_stats`
WHERE ticker = "przejęty";

-- STEP 7 — the identity row.
DELETE FROM `puls-gpw.espi_ebi.companies`
WHERE ticker = "przejęty";


-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 8 — Verify. This must now return ONLY the two -PDA rows, which are a
-- separate, out-of-scope quirk (correctly parsed from single-group headings).
-- ─────────────────────────────────────────────────────────────────────────────
SELECT ticker, name
FROM `puls-gpw.espi_ebi.companies`
WHERE NOT REGEXP_CONTAINS(ticker, r"^[A-Z0-9]{1,10}$")
ORDER BY ticker;

-- ZAB now carries the isin and the profile URL.
SELECT ticker, name, isin, hop_url
FROM `puls-gpw.espi_ebi.companies`
WHERE ticker = "ZAB";

-- And the two previously-orphaned sessions are present under ZAB.
SELECT snapshot_date, kurs_zamkniecia, source
FROM `puls-gpw.espi_ebi.company_daily_stats`
WHERE ticker = "ZAB" AND snapshot_date BETWEEN "2026-07-23" AND "2026-07-31"
ORDER BY snapshot_date;
