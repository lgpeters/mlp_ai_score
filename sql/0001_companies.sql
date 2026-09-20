create table if not exists companies (
    company_id  uuid primary key default gen_random_uuid(),
    cik         text unique not null,
    ticker      text unique not null,
    name        text not null,
    stockanalysis_id text,  -- stockanalysis.com URL slug for this company; currently == ticker.lower()
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);
