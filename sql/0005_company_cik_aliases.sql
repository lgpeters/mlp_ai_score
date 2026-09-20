-- Known historical CIKs for a company that has since re-registered under a
-- different CIK (e.g. a go-private/re-IPO under a new legal entity). Kept
-- separate from `companies` since a company has exactly one *current* CIK,
-- but may have zero or more historical ones we still want filings for.
create table if not exists company_cik_aliases (
    cik        text primary key,
    company_id uuid not null references companies(company_id),
    note       text,
    created_at timestamptz not null default now()
);

create index if not exists company_cik_aliases_company_id_idx on company_cik_aliases (company_id);
