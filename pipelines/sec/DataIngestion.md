# SEC — Data Ingestion

## .methodology

1. Universe of 9 companies loaded from `data/universe.csv` into `companies` (ticker, CIK, legal name); a separate `company_cik_aliases` table tracks historical CIKs for companies that re-registered under a new entity (currently just Dell).
2. `company_info.py` pulls entity-level metadata (SIC code, tickers, exchanges, fiscal year end, former names) from EDGAR's `submissions` endpoint into `sec_companies`, one row per CIK (current + historical aliases).
3. `filings_schedule.py` pulls each CIK's full filing history from EDGAR and filters to a fixed `FORM_TYPES` set: `10-K`, `10-K/A`, `10-Q`, `10-Q/A`, `8-K`, `8-K/A`, `S-4`, `S-4/A`, `20-F`, `20-F/A`.
4. Date window starts `2012-01-01` — a safety buffer ahead of the real 2015-present analysis window, since a filing's `acceptanceDateTime` can lag the fiscal period it reports on by weeks.
5. `8-K` is narrowed to items `{1.01, 2.01, 2.02, 5.02, 7.01, 8.01}` (material agreements, completed acquisitions, earnings, exec appointments, investor disclosures, product/partnership news) — chosen for AI-business-engagement relevance, excluding purely administrative/governance items.
6. `content_id` is derived deterministically from `(data_source, accession_number)` via `shared/ids.py`, shared between `content` and `sec_filings` so both rows can be written without a round trip.
7. Historical CIK aliasing (`company_cik_aliases`) lets a company's pre-reorganization filings land under the same `company_id` as its current filings — currently used for Dell's pre-2013 LBO era.
8. `download_markdown.py` fetches and converts filings via `edgartools`, validates output against a minimum-length threshold to catch broken/empty responses, and uploads to Storage at `sec_edgar/{ticker}/{accession_number}.md`.
9. Every table upserts on a natural/deterministic key; `content.storage_path is null` means "not yet downloaded," making reruns and retries safe by construction.
10. `company_info.py`, `filings_schedule.py`, and `download_markdown.py` all expose a `run()` entrypoint and chain through `pipelines/run_all.py`.

## .coverage_report

- 9 companies, 10 CIKs (9 current + 1 historical alias for Dell).
- ~2,194 filings across all form types after the 8-K narrowing: roughly 8-K ~1,681, 10-Q ~362, 10-K ~123, S-4/S-4-A ~21, 20-F 7.
- Full markdown downloaded for 100% of discovered filings (129 in the initial 10-K/20-F backfill, remainder in the full sweep) — 0 outstanding download failures.
- Date range covered: 2012–present, subject to the real gaps below.

## .assumptions_tradeoffs

- Narrowed `8-K` to 6 item codes to balance signal against noise/cost — accepts the risk of missing a genuinely relevant filing tagged with an item code outside that set.
- Used a 2012 buffer rather than a strict 2015 start, since `filingDate`/`acceptanceDateTime` can trail the actual fiscal period (`reportDate`) by weeks to months.
- Chose deterministic `content_id` (hashed from accession number) over a random UUID specifically so `content` and `sec_filings` rows can be written together without a `RETURNING`/lookup round trip.
- Built CIK aliasing as a manual, explicit table rather than a fully generalized multi-CIK-per-company system — proportionate to the one confirmed case (Dell) in a 9-company universe, not meant to auto-detect future cases.
- Kept `S-4`/`S-4-A` in scope despite low volume and mostly-boilerplate content, since the target company's business description in an acquisition registration is a high-confidence signal when the deal is AI-related.

## .known_issues

- **Dell, Nov 2013 – Nov 2015**: real gap — company was private, no filings exist anywhere under any entity. Not fixable by finding another CIK.
- **Atlassian (TEAM)**: no filings before its Dec 2015 IPO — expected boundary, not a gap. Filed `20-F` (foreign private issuer) rather than `10-K`/`10-Q`/`8-K` until its Sept 2022 Delaware redomiciliation, same CIK throughout.
- The 6-item `8-K` filter is a judgment call, not exhaustive — a filing with genuine AI-relevant content tagged under an excluded item code would be silently skipped.
- See `COVERAGE_EXCEPTIONS.md` for the full narrative behind the Dell and Atlassian cases.
