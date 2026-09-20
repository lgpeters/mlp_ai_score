-- How a company is actually referred to casually, beyond its formal legal
-- name in `companies.name` -- e.g. "Nvidia" (not "Nvidia Corporation"),
-- or multiple names for the same company: "P&G" and "Procter & Gamble".
-- One-to-many, unlike `companies.name` (exactly one legal name per row).
-- Used for sources like HN where the legal name rarely appears verbatim.
create table if not exists company_aliases (
    company_id  uuid not null references companies(company_id),
    alias       text not null,
    primary key (company_id, alias)
);

create index if not exists company_aliases_company_id_idx on company_aliases (company_id);
