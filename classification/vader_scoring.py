"""Blind, context-free general-sentiment baseline for every chunk, using
VADER (nltk) -- a rule-based lexicon scorer with zero concept of "AI" or
"company." Used to demonstrate AI-conditioned valence isn't just detecting
generic positive/negative language (Stage 3).

Writes only vader_compound to chunk_classifications -- every other
classification column (company_relevant, ai_category, etc.) is left
untouched, whether null (not yet scored by the LLM) or already populated
(scored independently, in either order). Idempotent via
WHERE vader_compound IS NULL.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import groupby

import nltk
from nltk.sentiment import SentimentIntensityAnalyzer

from shared.db import get_connection
from shared.storage import get_source_text

nltk.download("vader_lexicon", quiet=True)
_sia = SentimentIntensityAnalyzer()

# The bottleneck is the per-document Storage fetch (network I/O), not VADER
# itself (pure lexicon lookup, negligible CPU) -- so this is I/O-bound and
# threads are the right tool (they release the GIL while waiting on the
# network). Each score_document() call opens its own DB connection and does
# its own Storage fetch, so documents are safe to process concurrently; the
# only shared state is _sia, which is read-only (stateless scoring calls).
MAX_WORKERS = 10

PENDING_SQL = """
    select ci.chunk_id, ci.content_id, ci.start_offset, ci.end_offset, c.type, c.storage_path
    from content_index ci
    join content c using (content_id)
    left join chunk_classifications cc using (chunk_id)
    where cc.chunk_id is null or cc.vader_compound is null
    order by ci.content_id
"""

UPSERT_SQL = """
    insert into chunk_classifications (chunk_id, vader_compound)
    values (%s, %s)
    on conflict (chunk_id) do update set vader_compound = excluded.vader_compound
"""


def get_pending() -> list[tuple[str, str, int, int, str, str]]:
    """Returns [(chunk_id, content_id, start_offset, end_offset, type, storage_path), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL)
            return cur.fetchall()


def score_document(content_id: str, rows: list[tuple[str, str, int, int, str, str]]) -> int:
    content_type, storage_path = rows[0][4], rows[0][5]
    text = get_source_text(content_id, content_type, storage_path)

    with get_connection() as conn:
        with conn.cursor() as cur:
            for chunk_id, _, start, end, _, _ in rows:
                compound = _sia.polarity_scores(text[start:end])["compound"]
                cur.execute(UPSERT_SQL, (chunk_id, compound))

    return len(rows)


def run(limit_documents: int | None = None) -> None:
    pending = get_pending()
    by_content_id = [(cid, list(group)) for cid, group in groupby(pending, key=lambda r: r[1])]

    if limit_documents is not None:
        by_content_id = by_content_id[:limit_documents]

    print(f"{len(by_content_id)} documents ({sum(len(g) for _, g in by_content_id)} chunks) to score")

    total = 0
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(score_document, cid, rows): cid for cid, rows in by_content_id}
        for future in as_completed(futures):
            content_id = futures[future]
            try:
                total += future.result()
            except Exception as e:
                print(f"  {content_id}: failed ({e})")
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(by_content_id)} documents done")

    print(f"{len(by_content_id)} documents, {total} chunks scored")


if __name__ == "__main__":
    run()
