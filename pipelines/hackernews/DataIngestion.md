# Hacker News — Data Ingestion

## .methodology

1. Source: the HN Algolia Search API (`hn.algolia.com`) — free, no auth, no signup required. Verified live and reliable before building against it (a competing option, newsrank.ai, was found broken — HTTP 525 — at evaluation time).
2. Content model is story metadata (title, url, points, author, num_comments, created_at) — **not** the linked external article's body, since HN doesn't host that. Avoids the scraping/ToS risk of following arbitrary external links; comment thread text is available via the same API but not yet ingested (noted as a legitimate future source, since it's first-party HN content).
3. Queried per company per calendar month (2015-01 through the current month, 141 months), using both the company's formal legal name and its colloquial aliases (`company_aliases` table), results merged and deduped by story `objectID` before ranking.
4. Aliases exist because the formal legal name alone returned **zero** real hits for genuinely newsworthy periods (e.g. `"Nvidia Corporation"` vs. how people actually write "Nvidia" casually) — confirmed empirically, not assumed.
5. Search confirmed case-insensitive empirically (`nvidia`/`Nvidia`/`NVIDIA` return identical results) — no separate case-variant aliases needed.
6. Ambiguous/common-word company names (Caterpillar, Waste Management) needed more specific phrasing to reduce pollution from unrelated meanings (literal insects, generic "waste management" usage) — tested and confirmed a real precision improvement, though not a full elimination.
7. **No relevance floor applied at ingestion** — deliberately loose. A genuine story with weak textual overlap could otherwise be dropped by an over-eager filter; relevance sense-checking is deferred to a cheap downstream LLM pass over headlines (headlines are a handful of tokens, so this is inexpensive) rather than attempted in the scraper.
8. Top 10 stories per company per month by `points`, after merging every alias's results — the **full** result set is fetched before ranking (not just Algolia's first page), since its default relevance order does not match points order.
9. `content_id` derived deterministically from `(data_source='hackernews.com', source_doc_id)`, where `source_doc_id` is the story's external URL, or the canonical HN discussion link as a fallback for self-posts (Ask HN/Show HN) that have no external URL.
10. `content.sub_type = 'title'` is a fixed category label (not the story's actual title, which lives in `hackernews.title`) — leaves room for a future `'comment'` sub-type if comment threads are ever ingested as a separate content stream. `created_at` is parsed from Algolia's `created_at_i` (unix timestamp), not the ISO string, to avoid format ambiguity.

## .coverage_report

- 9,847 total stories across all 9 companies, 2015-01 through 2026-09 (141 months).
- Tech-forward names (ADBE, DELL, INTC, MU, NVDA, PG, TEAM) sit near or at the 10-per-month ceiling for most months (1,114-1,410 stories each).
- Caterpillar (159) and Waste Management (131) are dramatically sparser — reflects genuinely low real-world HN discussion volume for industrial/consumer-staples companies, not a scraper defect.
- Full content download (markdown/comments) intentionally **not yet done** — a `points`-based filter is planned before committing to downloading full content for all ~9,847 candidate stories.

## .assumptions_tradeoffs

- Accepted noisy/loose metadata ingestion in exchange for zero risk of false-negative filtering at this stage — precision work is deferred to the cheap LLM headline sense-check, not attempted here.
- Query is necessarily title/discussion-level (company name + AI-adjacent terms would need to be combined at the classification stage, not the ingestion stage, since a "top 10 by points" per month is a generic company-relevance sweep, not an AI-topic-filtered one) — HN doesn't expose full article bodies regardless, so signal is bounded by title and eventual comment-thread content only.
- Comment thread text deliberately not ingested yet — a legitimate, low-risk (first-party, no ToS issue) content source held for a later pass rather than built speculatively now.

## .known_issues

- Caterpillar and Waste Management's sparse coverage is a real characteristic of the HN community's interests, not a defect — confirmed by testing both bare and exact-phrase queries and finding genuinely little relevant discussion either way.
- Ambiguous company names still carry residual noise risk even after alias tuning (not fully eliminated) — a title matching the query terms isn't guaranteed to genuinely be about the company; deferred to downstream LLM filtering rather than solved here.
- No relevance floor at ingestion means a meaningful fraction of the 9,847 stories are likely off-topic filler, especially in low-coverage company/months — expected and intentional, not accidental.
- Full content (article body, comments) is not yet downloaded — this covers metadata ingestion only; the download phase is pending a `points` threshold to be defined before committing storage/processing budget to it.
