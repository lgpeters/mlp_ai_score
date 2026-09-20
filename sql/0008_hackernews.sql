-- Raw story metadata pulled from the HN Algolia Search API, monthly top-10
-- per company by points. content_id is populated once the shared
-- content-table write is specified; nullable for now.
--
-- hn_id is deterministic: "{ticker}_{story_id}", not a random uuid -- the
-- same story can legitimately appear under more than one company (a story
-- mentioning two of our tickers), so the natural key is (ticker, story_id),
-- not story_id alone.
create table if not exists hackernews (
    hn_id         text primary key,
    content_id    uuid references content(content_id),
    ticker        text not null,
    story_id      text not null,
    query_month   date not null,
    title         text not null,
    url           text,
    author        text,
    points        int,
    num_comments  int,
    created_at    timestamptz,
    story_text    text,
    unique (ticker, story_id)
);

create index if not exists hackernews_content_id_idx on hackernews (content_id);
