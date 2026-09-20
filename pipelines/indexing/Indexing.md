# Indexing (Embeddings) — Data Ingestion

## .methodology

1. Embedding model: **`FinLang/finance-embeddings-investopedia`** — free, local, finance-domain-tuned `sentence-transformers` model (768-dim). Chosen over paid Voyage AI (`voyage-finance-2`) after evaluating cost (both genuinely trivial — under ~£10 even in a worst case), setup friction (Voyage, and Vercel's AI Gateway as an alternative route to it, both required a credit card even on nominally-free tiers — confirmed by actually trying), and evidence (no direct independent benchmark exists comparing the two specifically; the closest related evidence, the independent FinMTEB benchmark, showed a different open finance-tuned model beating Voyage's *general-purpose* model, which weakened rather than strengthened the case for paying).
2. Storage: `content_index.finlang_embedding` is a **`halfvec(768)`** column (2 bytes/dimension) rather than a full `vector(768)` (4 bytes/dimension) — halves embedding storage, a deliberate lever alongside offset-only chunk storage to stay under Supabase's free-tier 500MB database limit.
3. **No ANN/HNSW index** — deliberate. Retrieval is always scoped to one document (`WHERE content_id = X`) first, so similarity search is an exact sequential scan over tens of chunks, not millions; an approximate index would be unnecessary overhead.
4. The embedding pipeline groups pending chunks by `content_id` before processing, downloading each document's markdown from Storage exactly once — not once per chunk — meaningful savings given some documents have 200+ chunks.
5. Idempotent via `WHERE finlang_embedding IS NULL` — reruns only touch unembedded chunks, and composes correctly with chunking's delete-then-reinsert behavior for free (re-chunking a document automatically invalidates its old embeddings, since new rows start with `embedding = NULL` by construction).
6. Batch-encodes all of a document's pending chunk texts in one `model.encode()` call rather than one at a time, for throughput.
7. Embeddings are written back via an explicit `::halfvec` cast on a hand-built pgvector literal string — psycopg2 has no native `vector`/`halfvec` adapter.
8. The `vector` Postgres extension was enabled specifically at version 0.8.2 to get `halfvec` support (requires ≥0.7.0).
9. A minimal Flask frontend (`frontend/app.py` + `index.html`) sits on top of this index: filters (company, type, sub_type, publication-date range) plus a free-text search box that embeds the query with the same FinLang model and returns top-5/bottom-5 documents by cosine distance, deduped to each document's single best-matching chunk.
10. Run once across the full backlog after settling on design: **1,035 documents, 67,398 of 67,426 chunks embedded**.

## .coverage_report

- 67,398 / 67,426 chunks embedded (~99.96%) — the small remainder is a handful of edge-case/empty chunks, not a failure.
- Covers all currently-downloaded SEC filings and transcripts. Hacker News content is not yet in scope — no download step has been run for it, per an explicit decision to sense-check the metadata first.

## .assumptions_tradeoffs

- Chose a free local model over a paid API primarily to eliminate signup/vendor friction (a card requirement was discovered for both Voyage directly and via Vercel's AI Gateway), not because of a clear, independently-verified quality advantage — the quality question was left genuinely open, not resolved in FinLang's favor by hard evidence.
- Skipped ANN-index setup complexity, valid specifically because retrieval is always document-scoped — would need revisiting if cross-document/corpus-wide search is ever required.
- Left room for a second embedding model (e.g. a future `voyage_embedding` `halfvec` column) by naming the column per-model rather than generically `embedding` — adding a model later is an additive column, not a migration.

## .known_issues

- No independent, apples-to-apples benchmark exists comparing FinLang specifically against Voyage's finance-specific model — the decision was made on cost/friction grounds; retrieval quality has only been spot-checked against a few real queries in the frontend (e.g. confirming an AI-strategy query surfaces genuinely relevant Intel/NVIDIA transcript passages), not rigorously evaluated.
- Transient Supabase pooler connectivity errors were observed during/after the large embedding run (likely connection-churn related, given ~1,035 open/close cycles across the job) — resolved on its own, but worth knowing if a similarly large batch job is run again.
- Hacker News content is entirely outside the current embedding scope (no markdown downloaded yet), so frontend search results are currently limited to SEC filings and transcripts only.
