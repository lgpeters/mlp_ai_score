create extension if not exists vector;

-- Chunk text isn't duplicated here -- start_offset/end_offset index into the
-- markdown file already in Storage (content.storage_path). Slice it out on
-- demand when a chunk is actually selected for the LLM.
--
-- chunk_id is deterministic: "{content_id}_{chunk_index}", not a random
-- uuid, so it's derivable without a lookup and inherently unique per
-- (content_id, chunk_index) by construction.
--
-- Embedding columns are named per model (finlang_embedding here) rather
-- than one generic "embedding" column, so adding another model for A/B
-- testing (e.g. voyage_embedding) is an additive column, not a migration.
create table if not exists content_index (
    chunk_id          text primary key,
    content_id        uuid not null references content(content_id),
    chunk_index       int not null,
    start_offset      int not null,
    end_offset        int not null,
    finlang_embedding halfvec(768),
    unique (content_id, chunk_index)
);

create index if not exists content_index_content_id_idx on content_index (content_id);
