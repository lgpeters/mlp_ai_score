-- Raw event metadata scraped from stockanalysis.com's per-ticker transcript
-- list page. One row per event (earnings calls and other event types alike
-- -- see is_earnings_call). content_id is populated once the shared
-- content-table write is run; nullable until then.
create table if not exists transcripts (
    content_id       uuid references content(content_id),
    ticker           text not null,
    fiscal_year      text,
    quarter_label    text,
    detail_slug      text not null,
    event_date       date,
    event_title      text,
    is_earnings_call boolean not null default false,
    url              text not null,
    unique (ticker, detail_slug)
);

create index if not exists transcripts_content_id_idx on transcripts (content_id);
