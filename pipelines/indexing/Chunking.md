# Chunking — Data Ingestion

## .methodology

1. `RecursiveCharacterTextSplitter` (`langchain-text-splitters`) — boundary-aware (paragraph → sentence → word priority), not naive fixed-width slicing.
2. Chunk size settled at **2000 characters (~500 tokens)** after iterating through 500/1000/3000 — the target was roughly "1-2 paragraphs" of context per chunk, calibrated against real measured paragraph lengths (SEC filings ~224 chars/paragraph; transcripts ~97 chars per fragmented speaker-turn "paragraph").
3. **10% overlap** (200 chars) — kept modest specifically because boundary-aware splitting reduces the overlap needed to avoid losing meaning at a cut, compared to what a naive splitter would require.
4. Only `start_offset`/`end_offset` are stored per chunk, not the chunk text itself — text is sliced on demand from the markdown already in Storage. Chosen specifically to stay under Supabase's free-tier 500MB database limit rather than duplicating the entire corpus a second time in Postgres.
5. `chunk_id` is deterministic: `"{content_id}_{chunk_index}"`, not a random UUID — matches the project-wide pattern of natural/derivable keys.
6. Table is `content_index` (renamed from an earlier `chunks`).
7. **Idempotency across a chunk-size change** was a real gap in an earlier version — a pure upsert left stale higher-index chunks behind from a previous, differently-sized run. Fixed with delete-then-reinsert per document, so a rerun always converges exactly to the current chunking parameters.
8. Reuses the existing `content.processed`/`processed_at` columns as the "has this document been chunked" flag, rather than adding a new one.
9. Only processes `content` rows where `storage_path is not null and processed = false` — depends on the download stage having already run.
10. Run once across the full corpus after settling on parameters: **1,036 documents → 67,426 chunks**.

## .coverage_report

- 1,036 / 1,036 downloaded documents chunked (100%), 0 pending.
- 67,426 total chunks across SEC filings and transcripts.

## .assumptions_tradeoffs

- One fixed chunk size across both content types, despite transcripts having much shorter native paragraphs than SEC filings — simplicity over per-type tuning; revisit if retrieval quality demands it.
- Traded a small amount of retrieval-time complexity (an extra Storage fetch when a chunk's text is actually needed) for a large one-time storage saving by not duplicating chunk text in Postgres.
- Chunk size was settled empirically through iteration against real document samples, not derived from a single principled calculation — balances embedding precision (favors smaller) against the LLM needing self-contained context for materiality/valence judgment (favors larger).

## .known_issues

- Neighbor-context retrieval (fetching a matched chunk plus its ±1 neighbors for fuller LLM context) was discussed as an option but not implemented — current design instead leans on the larger 2000-char chunk size being self-contained on its own.
- Length-scaled retrieval count (number of candidate chunks scaling with document length) was discussed but superseded by a different design: a relevance "back-off" (stop after N consecutive LLM-judged-irrelevant chunks) — that logic belongs to the retrieval/scoring stage, not chunking, and is not yet built.
