-- Internal bookkeeping for classification/news/LLM_Filtering.py. Each row
-- is one request submitted within an Anthropic Batch, covering a group of
-- chunks scored together in one call (to amortize the cached system
-- prompt). custom_id is too short (Anthropic-imposed limit) to encode the
-- chunk_ids directly, so they're tracked here instead, in the same order
-- sent to the API -- needed to zip the batch's array-of-results response
-- back to the right rows once results are fetched, potentially in a later
-- process. Generic (chunk_id, not hn_id) so it works for any source's
-- chunks, not just Hacker News.
create table if not exists chunk_batch_requests (
    batch_id  text not null,
    custom_id text not null,
    chunk_ids text[] not null,
    -- Which round this request belongs to (classification/sec's
    -- round-based RAG search) -- lets fetch_round() stamp the right round
    -- number onto chunk_classifications without the caller needing to
    -- pass it back in separately after the async batch gap.
    round         int,
    -- Actual token usage + model for this request, read directly off the
    -- Anthropic Batch result's own usage/model fields at fetch time (not
    -- estimated) -- lets cost be computed per batch/round/module after
    -- the fact, e.g. sum(input_tokens), sum(output_tokens) grouped by
    -- round or by joining back to chunk_classifications for a per-source
    -- breakdown. Null until fetch_round() processes this request (or if
    -- it failed/was never a "succeeded" result).
    input_tokens  int,
    output_tokens int,
    model         text,
    primary key (batch_id, custom_id)
);
