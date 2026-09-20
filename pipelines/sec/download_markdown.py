import hashlib

from edgar import get_by_accession_number, set_identity

from shared.db import get_connection
from shared.storage import upload_text

set_identity("mlp_ai_score lukepeters98@icloud.com")

DATA_SOURCE = "sec_edgar"

# Below this, treat the response as broken rather than a genuine filing --
# catches empty bodies, error/placeholder pages, and other silent failures
# from the fetch or markdown conversion. Real filings in our form set
# (10-K, 10-Q, 8-K, 20-F, S-4, ...) are always at least a few thousand
# characters; this threshold is well below the smallest of those, so it
# only trips on genuinely bad responses.
MIN_MARKDOWN_LENGTH = 500


class DownloadError(Exception):
    pass

PENDING_SQL = """
    select c.content_id, f.accession_number, co.ticker
    from content c
    join sec_filings f using (content_id)
    join companies co using (company_id)
    where c.data_source = %(data_source)s and c.storage_path is null
      and (%(forms)s::text[] is null or f.form = any(%(forms)s))
    order by c.publication_date desc
"""

MARK_DOWNLOADED_SQL = """
    update content
    set storage_path = %s, content_hash = %s, downloaded_at = now()
    where content_id = %s
"""


def get_pending(forms: list[str] | None = None) -> list[tuple[str, str, str]]:
    """Returns [(content_id, accession_number, ticker), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL, {"data_source": DATA_SOURCE, "forms": forms})
            return cur.fetchall()


def download_one(content_id: str, accession_number: str, ticker: str) -> None:
    try:
        filing = get_by_accession_number(accession_number)
    except Exception as e:
        raise DownloadError(f"fetch failed: {e}") from e

    if filing is None:
        raise DownloadError("no filing returned for this accession number")

    try:
        md = filing.markdown()
    except Exception as e:
        raise DownloadError(f"markdown conversion failed: {e}") from e

    if not md or len(md.strip()) < MIN_MARKDOWN_LENGTH:
        actual_len = len(md.strip()) if md else 0
        raise DownloadError(f"content too short to be genuine ({actual_len} chars, expected >= {MIN_MARKDOWN_LENGTH})")

    content_hash = hashlib.sha256(md.encode("utf-8")).hexdigest()
    storage_path = f"{DATA_SOURCE}/{ticker}/{accession_number}.md"
    upload_text(storage_path, md)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(MARK_DOWNLOADED_SQL, (storage_path, content_hash, content_id))

    print(f"  ok    {accession_number} -> {storage_path} ({len(md):,} chars)")


def run(limit: int | None = None, forms: list[str] | None = None) -> None:
    pending = get_pending(forms=forms)
    if limit is not None:
        pending = pending[:limit]

    print(f"{len(pending)} filings to download")

    failures = []
    for content_id, accession_number, ticker in pending:
        try:
            download_one(content_id, accession_number, ticker)
        except DownloadError as e:
            print(f"  FAIL  {accession_number}: {e}")
            failures.append((accession_number, str(e)))

    print(f"\n{len(pending) - len(failures)} succeeded, {len(failures)} failed")
    if failures:
        print("failed accession numbers (left as storage_path=null, safe to retry):")
        for accession_number, reason in failures:
            print(f"  {accession_number}: {reason}")


if __name__ == "__main__":
    run()
