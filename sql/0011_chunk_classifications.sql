-- Stands alone from any specific source table (hackernews, sec_filings,
-- transcripts) -- keyed on content_index.chunk_id so the same classifier
-- pipeline (classification/news/LLM_Filtering.py) generalizes across every
-- source's chunks, not just Hacker News.
create table if not exists chunk_classifications (
    -- on delete cascade: a content_index row can legitimately be deleted by
    -- upstream cleanup (e.g. pipelines/hackernews/news_schedule.py's
    -- cleanup_non_matching/dedup_titles) even after it's been classified --
    -- without cascade, that delete would hit a foreign key violation and
    -- abort the whole cleanup transaction.
    chunk_id             text primary key references content_index(chunk_id) on delete cascade,
    -- Two-pass design: a cheap first pass (small/fast model) sets
    -- company_relevant + ai_associated only. A second, smarter-model pass
    -- runs ONLY on rows where both are true, filling in category/valence --
    -- the expensive judgment is never spent on rows the cheap gate already
    -- ruled out.
    company_relevant     boolean,
    -- cheap-pass gate: 0=No, 1=Core AI (the content is fundamentally about
    -- AI/ML itself), 2=Infra/Compute (AI-enabling hardware/chips/data
    -- centers -- e.g. GPUs -- without the content being about AI itself),
    -- 3=Platform (a software platform/tool/API that enables AI use, e.g.
    -- a cloud AI platform or foundation-model API). Not a plain boolean --
    -- distinguishing these matters most for the infra-heavy names in this
    -- universe (NVDA, INTC, MU, DELL), where "AI" often shows up only as
    -- adjacent hardware/platform context rather than as the subject itself.
    ai_associated        int,
    ai_category          int,   -- 1-6, see classification/ClassDescriptions.md -- only set once ai_associated is nonzero
    ai_materiality_score int,   -- 0-10, within whichever category applies
    ai_saliency_score    int,   -- -3 to 3, business-impact direction (not writing tone)
    batch_id             text,  -- set while submitted to an in-flight Anthropic Batch, cleared once scored
    scored_at            timestamptz,
    -- Which round (classification/sec/LLM_Filtering.py's round-based RAG
    -- candidate search) this chunk was most recently submitted/scored in.
    -- Not used by the news/HN pipeline (every HN chunk is classified in
    -- one pass, there's no round concept there) -- null for those rows.
    -- No automated stopping rule reads this; query it directly (group by
    -- content_id/round, look at each round's hit rate) to decide whether
    -- a document is worth another round.
    round                int,
    -- Blind, context-free general-sentiment baseline (VADER compound score,
    -- -1 to 1) -- see classification/vader_scoring.py. Deliberately
    -- unrelated to AI relevance: used to demonstrate AI valence isn't just
    -- generic sentiment (Stage 3). Written independently of the LLM
    -- columns above -- either can be populated first, neither overwrites
    -- the other.
    vader_compound       real
);
