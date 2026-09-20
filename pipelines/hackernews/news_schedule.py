from datetime import date, datetime, timezone

import requests

from shared.db import get_connection
from shared.ids import content_id_for

BASE_URL = "https://hn.algolia.com/api/v1/search"
START_DATE = date(2015, 1, 1)
TOP_N = 10
DATA_SOURCE = "hackernews.com"

# Full set for the month, not a guessed page size -- points-based ranking
# needs every candidate in hand before sorting, not just the top page by
# Algolia's default relevance order (confirmed empirically: the true top-N
# by points changes once the full result set is fetched).
HITS_PER_PAGE = 1000

_PUNCTUATION = ",.!?;:()[]\"'-"


def _contains_word(title: str, word: str) -> bool:
    """Whole-word containment, not substring -- "micron" must not match
    inside "micronation"/"Micronaut". Padding with spaces (and normalizing
    punctuation to spaces) handles word-at-the-edge and punctuation-adjacent
    cases without needing a regex."""
    padded = f" {title.lower()} "
    for ch in _PUNCTUATION:
        padded = padded.replace(ch, " ")
    return f" {word.lower()} " in padded

CONTENT_UPSERT_SQL = """
    insert into content (content_id, company_id, type, sub_type, publication_date, data_source, source_doc_id)
    values (%s, %s, %s, %s, %s, %s, %s)
    on conflict (data_source, source_doc_id) do update set
        publication_date = excluded.publication_date
"""

HACKERNEWS_UPSERT_SQL = """
    insert into hackernews (
        hn_id, content_id, ticker, story_id, query_month, title, url, author, points, num_comments, created_at, story_text
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (hn_id) do update set
        content_id = excluded.content_id,
        title = excluded.title,
        points = excluded.points,
        num_comments = excluded.num_comments,
        story_text = excluded.story_text
"""


def get_universe() -> list[tuple[str, str, list[str]]]:
    """Returns [(ticker, company_id, [name, *aliases]), ...] -- queries per
    company are every alias plus the formal name, merged before ranking."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select ticker, company_id, name from companies")
            companies = cur.fetchall()

            cur.execute("select company_id, alias from company_aliases")
            aliases_by_company: dict[str, list[str]] = {}
            for company_id, alias in cur.fetchall():
                aliases_by_company.setdefault(company_id, []).append(alias)

    return [
        (ticker, company_id, [name, *aliases_by_company.get(company_id, [])])
        for ticker, company_id, name in companies
    ]


def month_starts(start: date, end: date) -> list[date]:
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(date(year, month, 1))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def fetch_stories(query: str, month_start: date) -> list[dict]:
    """All matches for one query in one month, unranked/untruncated."""
    month_end = _next_month(month_start)
    start_i = int(datetime(month_start.year, month_start.month, 1, tzinfo=timezone.utc).timestamp())
    end_i = int(datetime(month_end.year, month_end.month, 1, tzinfo=timezone.utc).timestamp())

    resp = requests.get(BASE_URL, params={
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{start_i},created_at_i<{end_i}",
        "hitsPerPage": HITS_PER_PAGE,
    })
    resp.raise_for_status()
    return resp.json()["hits"]


def fetch_top_stories(queries: list[str], month_start: date) -> list[dict]:
    """Merges results across every query (name + aliases) for one company/month,
    dedupes by objectID (the same story can match more than one alias), filters
    to genuine literal name/alias matches BEFORE ranking (not after storage --
    otherwise high-point noise, e.g. Microsoft stories crowding out Micron, can
    crowd a genuine lower-point story out of the top N before it's ever stored,
    which the post-hoc cleanup step can never recover), then ranks by points
    and returns the top N."""
    by_id: dict[str, dict] = {}
    for query in queries:
        for hit in fetch_stories(query, month_start):
            by_id[hit["objectID"]] = hit

    literal_matches = [
        hit for hit in by_id.values()
        if any(_contains_word(hit.get("title") or "", q) for q in queries)
    ]

    hits = sorted(literal_matches, key=lambda h: h.get("points") or 0, reverse=True)
    return hits[:TOP_N]


def push_stories(ticker: str, company_id: str, query_month: date, stories: list[dict]) -> int:
    content_rows = []
    hackernews_rows = []

    for s in stories:
        if not s.get("title"):
            continue  # title is NOT NULL -- skip the rare story with no title rather than error

        story_id = s["objectID"]
        # created_at_i (unix timestamp) rather than parsing the created_at
        # string -- avoids any ambiguity/edge case in the string format.
        created_at = datetime.fromtimestamp(s["created_at_i"], tz=timezone.utc)

        # self-posts (Ask HN, Show HN text posts) have no external url --
        # content.source_doc_id is not null, so fall back to the story's own
        # HN discussion link, which is always a valid unique identifier.
        source_doc_id = s.get("url") or f"https://news.ycombinator.com/item?id={story_id}"

        content_id = content_id_for(DATA_SOURCE, source_doc_id)

        content_rows.append((
            content_id, company_id, "hackernews", "title", created_at, DATA_SOURCE, source_doc_id,
        ))
        hackernews_rows.append((
            f"{ticker}_{story_id}", content_id, ticker, story_id, query_month,
            s["title"], s.get("url"), s.get("author"),
            s.get("points"), s.get("num_comments"), created_at, s.get("story_text"),
        ))

    if not hackernews_rows:
        return 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(CONTENT_UPSERT_SQL, content_rows)
            cur.executemany(HACKERNEWS_UPSERT_SQL, hackernews_rows)

    return len(hackernews_rows)


# Whole-word containment, not plain substring -- ILIKE '%micron%' would
# also match inside "micronation"/"Micronaut". Padding both the title and
# the name/alias with spaces (punctuation normalized to spaces first) checks
# for the name/alias as its own token, same approach as _contains_word in
# Python above.
DELETE_NON_MATCHING_HACKERNEWS_SQL = r"""
    with padded as (
        select hn_id, ticker,
               ' ' || regexp_replace(lower(title), '[,.!?;:()"''-]', ' ', 'g') || ' ' as norm_title
        from hackernews
    )
    delete from hackernews h
    using padded p
    where h.hn_id = p.hn_id
      and not exists (
          select 1 from companies co
          where co.ticker = p.ticker
            and (
                p.norm_title like '% ' || lower(co.name) || ' %'
                or exists (
                    select 1 from company_aliases ca
                    where ca.company_id = co.company_id
                      and p.norm_title like '% ' || lower(ca.alias) || ' %'
                )
            )
      )
"""

# Careful ordering: a story can legitimately belong to more than one
# company (matched under one ticker's name/alias, not another's) -- only
# delete content_index/content once NO hackernews row references that
# content_id anymore, not just because the row that triggered the delete
# didn't match.
DELETE_ORPHANED_CONTENT_INDEX_SQL = """
    delete from content_index
    where content_id in (
        select c.content_id from content c
        where c.data_source = 'hackernews.com'
          and not exists (select 1 from hackernews h where h.content_id = c.content_id)
    )
"""

DELETE_ORPHANED_CONTENT_SQL = """
    delete from content
    where data_source = 'hackernews.com'
      and not exists (select 1 from hackernews h where h.content_id = content.content_id)
"""


# Keeps exactly one row per (ticker, title) -- the highest-points one, tied
# broken deterministically by hn_id. Genuine HN reposts (same title,
# different story_id, different date -- confirmed real, not a pipeline bug)
# still count as one representative occurrence for classification purposes,
# so this doesn't over-weight a single story just because it was resubmitted
# multiple times within a short window.
DELETE_DUPLICATE_TITLES_SQL = """
    delete from hackernews h
    where exists (
        select 1 from hackernews h2
        where h2.ticker = h.ticker
          and h2.title = h.title
          and (h2.points > h.points or (h2.points = h.points and h2.hn_id < h.hn_id))
    )
"""


def dedup_titles() -> None:
    """Collapses genuine HN reposts (same title under the same ticker,
    different story_id) down to their single highest-points occurrence, then
    cascades through content_index/content for anything now orphaned."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(DELETE_DUPLICATE_TITLES_SQL)
            deleted_hn = cur.rowcount
            cur.execute(DELETE_ORPHANED_CONTENT_INDEX_SQL)
            cur.execute(DELETE_ORPHANED_CONTENT_SQL)
            deleted_content = cur.rowcount

    print(f"dedup: removed {deleted_hn} duplicate-title hackernews rows, {deleted_content} orphaned content rows")


def cleanup_non_matching() -> None:
    """Drops any hackernews row whose title doesn't genuinely contain its
    company's name or an alias (e.g. "Microduck" under MU/Micron, pulled in
    by Algolia's loose matching, not a real mention) -- then cascades that
    cleanup up through content_index and content for anything now orphaned."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(DELETE_NON_MATCHING_HACKERNEWS_SQL)
            deleted_hn = cur.rowcount
            cur.execute(DELETE_ORPHANED_CONTENT_INDEX_SQL)
            cur.execute(DELETE_ORPHANED_CONTENT_SQL)
            deleted_content = cur.rowcount

    print(f"cleanup: removed {deleted_hn} non-matching hackernews rows, {deleted_content} orphaned content rows")


def run() -> None:
    universe = get_universe()
    months = month_starts(START_DATE, date.today())

    total = 0
    for ticker, company_id, queries in universe:
        print(f"{ticker} (queries: {queries}):")
        ticker_total = 0
        for month_start in months:
            stories = fetch_top_stories(queries, month_start)
            n = push_stories(ticker, company_id, month_start, stories)
            ticker_total += n
        total += ticker_total
        print(f"  {ticker_total} stories across {len(months)} months")

    print(f"\n{total} total stories upserted across {len(universe)} companies")

    cleanup_non_matching()
    dedup_titles()


if __name__ == "__main__":
    run()
