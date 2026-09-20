# SEC pipelines

Ingests SEC EDGAR company and filing metadata for the company universe into
Supabase. Purpose: build a dataset of SEC disclosures for the companies in
`companies`, scoped to the filing types most likely to carry substantive
business narrative (in particular, discussion of AI as a strategic focus),
while keeping ingestion volume manageable on a small budget.

## Modules

- **`company_info.py`** — pulls entity-level metadata (SIC code, tickers,
  exchanges, fiscal year end, former names, ...) from EDGAR's `submissions`
  endpoint into `sec_companies`, one row per CIK.
- **`filings_schedule.py`** — pulls each CIK's full filing history from
  EDGAR, filters it down (see below), and writes the result into `content`
  (shared metadata, one row per document) and `sec_filings` (SEC-specific
  fields), linked by a shared `content_id`.

Both expose a `run()` entrypoint and are idempotent — safe to re-run;
`content_id` is derived deterministically from `(data_source,
source_doc_id)` via `shared.ids.content_id_for`, and both tables upsert on
their natural keys rather than inserting duplicates.

## What gets ingested, and why

**Form types** (`FORM_TYPES` in `filings_schedule.py`):

| Form | Why |
|---|---|
| `10-K`, `10-K/A` | Annual report — Item 1 (Business), Item 1A (Risk Factors), Item 7 (MD&A). Highest narrative signal density, filed once a year. |
| `10-Q`, `10-Q/A` | Quarterly report — MD&A updates between annual reports. |
| `20-F`, `20-F/A` | Foreign private issuer's annual report — the equivalent of a 10-K for a company that was foreign-registered for part of its history (e.g. Atlassian was a UK plc until its 2022 Delaware redomiciliation, and filed 20-F instead of 10-K up to that point). Needed for continuous annual-report coverage across that kind of entity change. |
| `8-K`, `8-K/A` | Event-driven disclosures — see narrowing rule below. |
| `S-4`, `S-4/A` | Merger/registration statements. Mostly legal/financial boilerplate, but the target's business description is included in prose — a strong, direct signal when the deal is an AI acquisition ("we are acquiring X, an artificial intelligence company specializing in..."). Low volume, so cheap to keep in full. |

**8-K narrowing**: 8-K is by far the highest-volume form, but most *items*
within it are purely administrative/legal (governance mechanics, listing
compliance, auditor changes, vote results) with no business-strategy
narrative — same ingestion/chunking cost as a substantive filing, zero
signal. A filing is kept only if its `items` field contains at least one of:

| Item | What it covers | Why it matters here |
|---|---|---|
| `1.01` | Entry into a material agreement | AI partnerships, licensing deals, joint ventures |
| `2.01` | Completion of an acquisition | A closed AI acquisition |
| `2.02` | Results of operations (earnings) | MD&A-style commentary, often includes AI mentions |
| `5.02` | Officer appointments/departures | e.g. a "Chief AI Officer" hire — a direct, explicit signal |
| `7.01` | Reg FD disclosure | Investor-day presentations, increasingly where companies lay out an AI strategy |
| `8.01` | Other events | Catch-all — product launches, AI partnership announcements |

Excluded as low-value for this purpose: `3.01`–`3.03` (listing/securities
compliance), `4.01` (auditor changes), `5.01`/`5.03`–`5.08` (governance
mechanics, bylaws, vote results), `6.xx` (asset-backed securities
administrivia). Any 8-K carrying at least one relevant item is stored in
full — the filter is on the *filing*, not the individual item, so a filing
tagged e.g. `5.02,7.01,9.01` is kept even though `9.01` alone wouldn't
qualify it. All 8-Ks matching none of the relevant items are discarded
before ever reaching `content`/`sec_filings`.

**Date window**: filings with `acceptanceDateTime` after `2012-01-01`
(`START_DATE`) are kept — a safety buffer ahead of the actual 2015-present
analysis window, since a filing's `acceptanceDateTime`/`filingDate` can lag
the fiscal period it reports on (`reportDate`) by weeks.

## Known data gaps (not pipeline bugs)

- **Dell, 2014–2016 (no 10-K)**: Dell went private in an October 2013 LBO,
  deregistering its original CIK. It had no SEC reporting obligation again
  until 2016 (debt issuance), then re-listed under a new legal entity,
  "Dell Technologies Inc" (new CIK), in 2018. `company_cik_aliases` tracks
  the old CIK (`0000826083`) alongside the current one so both eras' filings
  land under the same `company_id` — but no filings exist for the private
  period under any entity, so the gap itself can't be filled.
- **Dell, Jan–Nov 2015**: falls inside the same go-private gap above.

## Historical CIK aliases

Some companies change CIK entirely when they re-register as a different
legal entity (go-private/re-IPO, corporate restructuring). `companies`
holds each company's *current* CIK only; `company_cik_aliases` holds any
known historical CIKs, each pointing at the same `company_id`. Both
`company_info.py` and `filings_schedule.py` iterate over
`shared.universe.get_cik_universe()`, which unions the two, so a company
with an alias gets both eras' data without any special-casing in the
pipeline code itself.

## Running

```bash
venv/bin/python -m pipelines.sec.company_info
venv/bin/python -m pipelines.sec.filings_schedule
```

Or as part of the full pipeline: `venv/bin/python -m pipelines.run_all`.
