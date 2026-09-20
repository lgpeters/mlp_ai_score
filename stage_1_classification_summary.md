# Stage 1 Classification Summary

# Total Cost : $20

Round-by-round results for the cheap first pass (`company_relevant` /
`ai_associated`), across the three corpora run to completion so far.
Round 0 is the full corpus size (not a real round) — everything after it
is what the round-based RAG search actually touched. Token counts are
summed directly from the Anthropic Batch API's own usage data
(`chunk_batch_requests.input_tokens`/`output_tokens`), not estimated.

## Earnings calls

| Round | Docs | Chunks | Hits | Tokens In | Tokens Out |
|---|---|---|---|---|---|
| 0 (total corpus) | 464 | 14,934 | — | — | — |
| 1 | 464 | 5,258 | 1,448 | 4,940,095 | 157,217 |
| 2 | 211 | 2,367 | 917 | 2,606,630 | 81,983 |
| 3 | 143 | 1,053 | 397 | 1,052,117 | 32,991 |
| 4 | 47 | 127 | 34 | 122,119 | 3,939 |
| 5 | 1 | 6 | 1 | 8,049 | 254 |

## 10-Q / 6-K

| Round | Docs | Chunks | Hits | Tokens In | Tokens Out |
|---|---|---|---|---|---|
| 0 (total corpus) | 440 | 51,082 | — | — | — |
| 1 | 440 | 6,186 | 669 | 5,459,091 | 192,798 |
| 2 | 132 | 2,320 | 378 | 2,116,394 | 72,322 |
| 3 | 82 | 1,463 | 187 | 1,384,791 | 45,727 |
| 4 | 46 | 831 | 111 | 790,015 | 26,129 |
| 5 | 29 | 522 | 57 | 510,817 | 16,405 |
| 6 | 18 | 301 | 18 | 299,195 | 9,383 |
| 7 | 5 | 77 | 0 | 78,446 | 2,407 |

## 10-K / 20-F

| Round | Docs | Chunks | Hits | Tokens In | Tokens Out |
|---|---|---|---|---|---|
| 0 (total corpus) | 127 | 36,881 | — | — | — |
| 1 | 127 | 2,205 | 394 | untracked* | untracked* |
| 2 | 59 | 1,142 | 305 | untracked* | untracked* |
| 3 | 45 | 906 | 212 | untracked* | untracked* |
| 4 | 40 | 812 | 162 | untracked* | untracked* |
| 5 | 33 | 686 | 108 | untracked* | untracked* |
| 6 | 28 | 585 | 88 | 522,430 | 18,398 |
| 7 | 22 | 458 | 57 | 416,297 | 14,341 |
| 8 | 17 | 353 | 40 | 330,329 | 11,142 |
| 9 | 10 | 207 | 9 | 193,517 | 6,559 |
| 10 | 2 | 41 | 1 | 40,374 | 1,318 |

\* Token usage tracking (`chunk_batch_requests.input_tokens`/`output_tokens`) was added mid-run, after round 5 of the 10-K/20-F pass had already completed — rounds 1-5's real token cost was never captured and can't be recovered retroactively.

## News (Hacker News)

Not round-based — every chunk was sent in a single full-corpus pass (`classification/news/LLM_Filtering.py`, before the round-based design existed), so there's no round column here. This also predates usage tracking entirely, so token counts are an **ESTIMATE**, not measured: content length is the actual sum of HN title characters, divided by 4 to approximate tokens; the system prompt (~658 tokens) is assumed repeated, uncached, across all 682 requests (`GROUP_SIZE=8`); output is a rough guess based on the small structured-JSON response shape, not derived from anything measured.

| Pass | Docs | Chunks | Hits | Tokens In (est.) | Tokens Out (est.) |
|---|---|---|---|---|---|
| 0 (total corpus) | 5,455 | 5,455 | — | — | — |
| 1 (single pass, all chunks) | 5,455 | 5,455 | 1,270 | ~565,000 (est.) | ~100,000 (est.) |

Tokens In (est.) breakdown: ~110,000-120,000 tokens of actual title/company-name content + ~448,000 tokens from the uncached system prompt repeated across 682 requests (658 tokens × 682). Tokens Out (est.) is a vague guess (~15-25 tokens per classified item × 5,455 items) — treat this row as order-of-magnitude only, not a real figure.
