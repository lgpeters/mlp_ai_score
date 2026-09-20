# Transcripts — Data Ingestion

## .methodology

1. Source: stockanalysis.com (earnings call transcripts sourced upstream from Quartr, a third-party vendor) — chosen after evaluating and rejecting FMP (transcripts paywalled behind Premium/Ultimate), API Ninjas (Premium-only), and earningscalls.dev (free API tier capped at 250-char previews, archive from 2020 only).
2. Selection was spot-checked before committing: verified real dates and full transcript text for ADBE/NVDA/TEAM at 2015/2022/2025 quarters, confirming coverage back to 2011-2015 depending on ticker.
3. `transcripts_schedule.py` scrapes each ticker's transcript-list page, regex-extracting an embedded non-JSON JS object literal (unquoted keys) that isn't exposed as a documented API — every event is captured (earnings calls and others alike), tagged `is_earnings_call` via an exact `"Q# YYYY"` title-pattern match.
4. `companies.stockanalysis_id` (currently `ticker.lower()`, verified to resolve correctly for all 9 tickers) links a company to its transcript-list URL.
5. `content_id` is derived deterministically from `(data_source, url)` — the transcript's own URL is the natural key.
6. `download_markdown.py` extracts full transcript text from the same embedded JS structure, reconstructing speaker/role/company attribution into markdown headers per speaker block.
7. A **legacy flat-body fallback** (`fullTranscriptBody` field) handles older-format pages that don't use the structured per-speaker block layout — discovered when 24 transcripts initially failed with "content too short," not silently accepted as a real gap.
8. Output is validated against a minimum-length threshold, so a broken/empty response is caught and left retryable (`storage_path` stays null) rather than marked done with garbage content.
9. Requests are rate-limited with a polite delay — stockanalysis.com returns 403s on rapid sequential requests without one.
10. Storage path: `transcripts/{ticker}/{content_id}.md`; every table upserts on a natural key, making reruns idempotent throughout.

## .coverage_report

- 896 total events discovered across all 9 companies (earnings calls plus AGMs, investor days, conferences, fireside chats, etc.).
- 100% download success — 872 succeeded on the first pass, the remaining 24 recovered once the legacy-format fallback was added.
- Earnings-call-specific counts per company range from ~61-66 for the long-public names down to 43 for Atlassian (2015 IPO) and 39-41 for Dell (see known issues).

## .assumptions_tradeoffs

- Accepted a third-party vendor's (Quartr) coverage limitations as a real, documented constraint rather than chasing a paid alternative — Voyage/FMP-style paid options were declined earlier in this project for cost/card-signup reasons.
- Ingested **every** event type at the metadata level (not just earnings calls), tagging `is_earnings_call` rather than filtering upfront — preserves optionality, since investor days and fireside chats may carry genuine AI-relevant content too.
- Regex-scraping an embedded, undocumented JS object (not a stable public API) is inherently fragile to upstream markup changes — accepted given no viable free/cheap official API alternative existed after evaluation.

## .known_issues

- **Caterpillar-style low-volume coverage isn't a transcript issue** — Caterpillar's transcript coverage is fine; the low-HN-volume finding was specific to Hacker News, not this source.
- **Dell**: transcript coverage on this source starts **2022**, a larger gap than the SEC-side private-company gap (2013-2015) — Dell traded under ticker **DVMT** (not DELL) from 2016-2018, and stockanalysis.com has no page for that ticker at all (confirmed 404, not indexed). Net effect: no usable Dell transcripts before 2022, despite the company being a real, calls-holding public entity for most of 2015 onward. See `pipelines/sec/COVERAGE_EXCEPTIONS.md` for the underlying corporate history.
- **Atlassian**: earliest transcript is 2016, consistent with its Dec 2015 IPO — real boundary, not a gap.
- Any further undiscovered page-format variants beyond the two already handled (structured blocks, legacy flat body) could still silently under-extract — mitigated, not eliminated, by the minimum-length validation.
- Full details in `COVERAGE_EXCEPTIONS.md`.
