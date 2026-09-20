# Data Coverage

Generated from live database queries (see `.queries_used` at the end of each
section for the exact SQL). Numbers reflect the state of the database as of
this document's last update — rerun the queries to refresh.

Universe: 9 companies.

| Ticker | Legal name | CIK | Colloquial alias(es) used for Hacker News |
|---|---|---|---|
| ADBE | Adobe Inc | 0000796343 | Adobe |
| CAT | Caterpillar Inc | 0000018230 | Caterpillar |
| DELL | Dell Inc | 0001571996 | Dell |
| INTC | Intel Corporation | 0000050863 | Intel |
| MU | Micron Technology Inc | 0000723125 | Micron |
| NVDA | Nvidia Corporation | 0001045810 | Nvidia |
| PG | The Procter & Gamble Company | 0000080424 | Procter & Gamble, P&G |
| TEAM | Atlassian Corporation | 0001650372 | Atlassian |
| WM | Waste Management Inc | 0000823768 | Waste Management |

---

## Primary vs. Secondary Sources

**Primary** — official company disclosures, the core evidentiary sources for this project:
- SEC Filings (EDGAR)
- Earnings Call Transcripts (stockanalysis.com)

**Secondary** — community discussion signal, used to gauge external attention/sentiment, not as a disclosure record:
- Hacker News

This distinction matters for how each source's noise should be read: false positives in a primary source are a real defect (a filing that shouldn't be there); false positives in the secondary source are an expected cost of casting a wide net over unstructured community discussion, and are filtered more aggressively as a result (see the Hacker News section).

---

## Primary Source: SEC Filings

**Dedup logic**: `content_id` is derived deterministically from `(data_source='sec_edgar', accession_number)` — SEC's own accession number is a genuine natural key, one per filing, so this can never produce a duplicate for the same filing regardless of how many times the pipeline reruns.

**Total**: 2,194 filings.

| Ticker | Count | Earliest | Latest |
|---|---|---|---|
| ADBE | 190 | 2012-01-26 | 2026-09-10 |
| CAT | 333 | 2012-01-26 | 2026-09-01 |
| DELL | 238 | 2012-01-06 | 2026-09-15 |
| INTC | 299 | 2012-01-19 | 2026-08-12 |
| MU | 303 | 2012-01-10 | 2026-08-26 |
| NVDA | 189 | 2012-02-15 | 2026-09-03 |
| PG | 371 | 2012-01-10 | 2026-08-04 |
| TEAM | 59 | 2016-08-17 | 2026-08-14 |
| WM | 212 | 2012-02-16 | 2026-08-26 |

**Form breakdown** (all companies): 8-K 1,654 · 10-Q 358 · 10-K 120 · 8-K/A 27 · S-4/A 13 · S-4 8 · 20-F 7 · 10-Q/A 4 · 10-K/A 3.

**Coverage bias**:
- 8-K is narrowed to 6 item codes (`1.01, 2.01, 2.02, 5.02, 7.01, 8.01`) chosen for AI-business-engagement relevance — this is a deliberate scope decision, not full 8-K coverage. A filing tagged only with an excluded item code is invisible to this dataset even if it exists on EDGAR.
- Date window starts 2012-01-01 (buffer ahead of the real 2015+ analysis window) — nothing before that date is in scope by design.

**Known issues**:
- **Dell**: real gap Nov 2013 – Nov 2015 — the company was private, no filings exist anywhere under any entity for that window. Not a pipeline defect.
- **Atlassian (TEAM)**: earliest filing 2016-08-17, consistent with its Dec 2015 IPO — a real boundary, not a gap. Filed `20-F` (foreign private issuer) rather than `10-K` until its Sept 2022 Delaware redomiciliation.
- Full detail: `pipelines/sec/COVERAGE_EXCEPTIONS.md`, `pipelines/sec/DataIngestion.md`.

**.queries_used**
```sql
select co.ticker, count(*) as n, min(c.publication_date)::date as earliest, max(c.publication_date)::date as latest
from content c join companies co using (company_id)
where c.data_source = 'sec_edgar'
group by co.ticker order by co.ticker;

select sub_type, count(*) as n from content where data_source = 'sec_edgar' group by sub_type order by n desc;
```

---

## Primary Source: Earnings Call Transcripts

**Source**: stockanalysis.com (sourced upstream from Quartr, a third-party vendor) — chosen after FMP, API Ninjas, and earningscalls.dev were all found paywalled or too shallow for the required date range.

**Dedup logic**: `content_id` derived from `(data_source='stockanalysis.com', url)` — each transcript's own page URL is the natural key.

**Total**: 896 transcripts (464 tagged `earnings_call`, 432 `other` — AGMs, investor days, conferences, fireside chats).

| Ticker | Count | Earliest | Latest |
|---|---|---|---|
| ADBE | 97 | 2011-09-20 | 2026-09-10 |
| CAT | 49 | 2019-04-24 | 2026-09-10 |
| DELL | 50 | 2021-05-27 | 2026-09-10 |
| INTC | 168 | 2010-07-13 | 2026-08-26 |
| MU | 140 | 2010-06-28 | 2026-08-27 |
| NVDA | 152 | 2010-05-13 | 2026-09-10 |
| PG | 78 | 2014-10-24 | 2026-09-10 |
| TEAM | 72 | 2016-02-04 | 2026-09-09 |
| WM | 90 | 2010-04-29 | 2026-07-29 |

**Coverage bias**: coverage depth is set by Quartr's own historical onboarding per ticker, not by the company's real-world history — a shallow start date here does not mean the company had no earnings calls before that date.

**Known issues**:
- **Caterpillar**: coverage starts 2019-04-24 despite being public since 1929 — a confirmed vendor coverage limitation (checked for a scraper/pagination bug; found none).
- **Dell**: coverage starts 2021-05-27 — a materially larger gap than the SEC-side private-company gap. Dell traded under ticker **DVMT** (not DELL) from 2016-2018; this source has no page for that ticker at all (confirmed 404). Net effect: no usable Dell transcripts before 2021, despite the company being a real, calls-holding public entity for most of that window.
- **Atlassian**: earliest transcript 2016-02-04, consistent with its Dec 2015 IPO — a real boundary.
- Full detail: `pipelines/transcripts/COVERAGE_EXCEPTIONS.md`, `pipelines/transcripts/DataIngestion.md`.

**.queries_used**
```sql
select co.ticker, count(*) as n, min(c.publication_date)::date as earliest, max(c.publication_date)::date as latest
from content c join companies co using (company_id)
where c.data_source = 'stockanalysis.com'
group by co.ticker order by co.ticker;

select sub_type, count(*) as n from content where data_source = 'stockanalysis.com' group by sub_type order by n desc;
```

---

## Secondary Source: Hacker News

**Source**: HN Algolia Search API (`hn.algolia.com`) — free, no auth, community discussion signal. Story metadata only (title, url, points, author) — not the linked external article's body, which HN doesn't host.

**Dedup logic**: `content_id` derived from `(data_source='hackernews.com', source_doc_id)`, where `source_doc_id` is the story's external URL, or its HN discussion page as a fallback for self-posts with no URL. A single story can appear under **more than one** `hackernews` row (once per company it was matched under) while sharing **one** `content` row — the dedup is on the underlying story, not on the per-company match.

**Three-stage filtering applied**, deliberately kept separate:
1. *Ingestion*: candidates merged across the company's name + aliases, then filtered to genuine literal name/alias matches (whole-word, not substring — see below) **before** ranking by `points`, then top 10 per company per month kept. Filtering before ranking (not after) matters: it was originally the other way around, and high-point noise (e.g. Microsoft stories crowding out Micron) could win the top-10 cut outright, silently losing genuine lower-point stories that a later cleanup pass could never recover since they were never stored.
2. *Cleanup* (`cleanup_non_matching()`): a safety-net rerun of the same whole-word name/alias check directly in SQL, for anything already stored under older logic.
3. *Dedup* (`dedup_titles()`): collapses genuine HN reposts (same title under the same ticker, different `story_id`, different date — confirmed real via spot-check, not a bug) down to each one's single highest-points occurrence, so a story resubmitted multiple times in a short window doesn't get over-weighted in downstream aggregation.

Both the ingestion-time filter and `cleanup_non_matching()` use **whole-word matching, not substring** — `ILIKE '%micron%'`-style substring checks were found to match *inside* unrelated words (`"micronation"`, `"Micronaut"`, `"intelligence"` matching "Intel"). The fix pads text with spaces (punctuation normalized to spaces first) and checks for `" name "` as its own token.

**Total after all three stages**: 5,684 stories (up from an earlier 3,503 once the crowding-out bug was fixed and previously-invisible genuine stories — especially Micron's — were recovered; up further from 0 for PG once the actually-used spelling, "Procter and Gamble" not "Procter & Gamble", was added as an alias).

| Ticker | Count | Earliest | Latest |
|---|---|---|---|
| ADBE | 1,059 | 2015-01-06 | 2026-09-08 |
| INTC | 1,364 | 2015-01-07 | 2026-09-18 |
| NVDA | 1,283 | 2015-01-05 | 2026-09-20 |
| DELL | 736 | 2015-01-06 | 2026-09-08 |
| TEAM | 584 | 2015-01-27 | 2026-09-01 |
| MU | 302 | 2015-02-02 | 2026-09-15 |
| CAT | 85 | 2015-03-06 | 2026-08-13 |
| WM | 28 | 2015-04-21 | 2026-04-17 |
| PG | 14 | 2015-04-03 | 2023-04-21 |

**Coverage bias**: two distinct findings, not one.
- **Micron's original near-zero count (4) was mostly a pipeline bug, not a real coverage gap** — the crowding-out issue (Microsoft's enormous HN volume beating Micron's genuine stories to the top-10 cut every month) meant real Micron coverage was being silently discarded before storage. Fixed; Micron now shows real, substantial coverage (302).
- **PG's near-zero count (originally 0-6) was genuinely a wrong-alias bug**, not a source limitation — HN's real coverage of the company almost entirely uses the spelled-out "Procter and Gamble," not the "&" symbol form our alias list had. Fixed; still the lowest-volume of the nine (14), which now looks like a real, modest characteristic of the source rather than an artifact.
- **Caterpillar (85) and Waste Management (28) remain genuinely low-volume** even after every fix — consistent with the same industrial/consumer-staples pattern found throughout this project (see `pipelines/sec/COVERAGE_EXCEPTIONS.md`, `pipelines/transcripts/COVERAGE_EXCEPTIONS.md`). This one looks like a real characteristic of the source, not a bug — checked via both bare and exact-phrase queries, both showing genuinely little discussion of either company.

**Known issues**:
- Even after cleanup/dedup, the literal-name-match filter can't tell "genuinely about the company" from "about the company, but not about AI" — that judgment is a separate, LLM-based stage (`classification/news/LLM_Filtering.py`, chunk-level, not yet run against this data).
- The literal-match filter still has a false-negative risk in the other direction: a story that refers to a company only by product name or an unlisted nickname (never the exact company name or a listed alias) would be wrongly dropped too.
- Full content (article body, comments) is not ingested — see `pipelines/hackernews/DataIngestion.md`. HN "content" for indexing purposes is the story title only, embedded directly (no downloaded markdown file), per `pipelines/indexing/Indexing.md`.
- Full detail: `pipelines/hackernews/DataIngestion.md`.

**.queries_used**
```sql
select co.ticker, count(*) as n, min(c.publication_date)::date as earliest, max(c.publication_date)::date as latest
from content c join companies co using (company_id)
where c.data_source = 'hackernews.com'
group by co.ticker order by co.ticker;

select count(*) from hackernews;  -- 5,684, after ingestion-time filtering + cleanup + dedup
```

---

## Cross-Source Summary

| Source | Type | Total docs | Dedup key |
|---|---|---|---|
| SEC Filings | Primary | 2,194 | `(sec_edgar, accession_number)` |
| Transcripts | Primary | 896 | `(stockanalysis.com, url)` |
| Hacker News | Secondary | 5,684 | `(hackernews.com, source_doc_id)` |

For the full reasoning behind every design decision summarized here — why each source was chosen, what alternatives were rejected and why, and the complete list of known issues — see the per-source `DataIngestion.md` files under `pipelines/sec/`, `pipelines/transcripts/`, and `pipelines/hackernews/`, plus `pipelines/indexing/Chunking.md` and `pipelines/indexing/Indexing.md` for how all three sources feed into the same downstream chunking/embedding pipeline.
