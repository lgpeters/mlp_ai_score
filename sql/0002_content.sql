create table if not exists content (
    content_id       uuid primary key default gen_random_uuid(),
    company_id       uuid not null references companies(company_id),
    type              text not null,
    sub_type          text,
    publication_date timestamptz,
    upload_date      timestamptz not null default now(),
    data_source      text not null,
    source_doc_id    text not null,   -- natural key from the source (SEC accession no. / RSS guid / transcript id)
    content_hash     text,             -- sha256 of raw content; set once downloaded. unchanged hash = skip re-chunk/re-embed
    storage_path     text,             -- path in Supabase Storage; null = not downloaded yet
    downloaded_at    timestamptz,
    processed        boolean not null default false,
    processed_at     timestamptz,
    metadata         jsonb,
    unique (data_source, source_doc_id)
);

create index if not exists content_company_id_idx on content (company_id);
