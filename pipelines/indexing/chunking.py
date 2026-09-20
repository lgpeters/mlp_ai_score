from langchain_text_splitters import RecursiveCharacterTextSplitter

from shared.db import get_connection
from shared.storage import download_text

CHUNK_SIZE_CHARS = 2000  # ~500 tokens
OVERLAP_CHARS = 200  # ~10%

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_CHARS, chunk_overlap=OVERLAP_CHARS, add_start_index=True
)

PENDING_SQL = """
    select content_id, storage_path
    from content
    where storage_path is not null and processed = false
    order by publication_date desc
"""

DELETE_EXISTING_SQL = "delete from content_index where content_id = %s"

CHUNK_UPSERT_SQL = """
    insert into content_index (chunk_id, content_id, chunk_index, start_offset, end_offset)
    values (%s, %s, %s, %s, %s)
    on conflict (chunk_id) do update set
        start_offset = excluded.start_offset,
        end_offset = excluded.end_offset
"""

MARK_PROCESSED_SQL = """
    update content set processed = true, processed_at = now() where content_id = %s
"""


def get_pending() -> list[tuple[str, str]]:
    """Returns [(content_id, storage_path), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL)
            return cur.fetchall()


def chunk_offsets(text: str) -> list[tuple[int, int]]:
    if not text:
        return []

    docs = _splitter.create_documents([text])
    return [(d.metadata["start_index"], d.metadata["start_index"] + len(d.page_content)) for d in docs]


def chunk_one(content_id: str, storage_path: str) -> int:
    text = download_text(storage_path)
    offsets = chunk_offsets(text)  # in document order -- enumerate() gives a properly incremental chunk_index

    rows = [(f"{content_id}_{i}", content_id, i, start, end) for i, (start, end) in enumerate(offsets)]

    with get_connection() as conn:
        with conn.cursor() as cur:
            # delete-then-reinsert (not a pure upsert) so a rerun with
            # different chunking parameters converges exactly to the new
            # scheme, instead of leaving stale higher-index chunks behind
            # from a previous, differently-sized chunking pass.
            cur.execute(DELETE_EXISTING_SQL, (content_id,))
            cur.executemany(CHUNK_UPSERT_SQL, rows)
            cur.execute(MARK_PROCESSED_SQL, (content_id,))

    return len(rows)


def run(limit: int | None = None) -> None:
    pending = get_pending()
    if limit is not None:
        pending = pending[:limit]

    print(f"{len(pending)} documents to chunk")

    total_chunks = 0
    for content_id, storage_path in pending:
        n = chunk_one(content_id, storage_path)
        total_chunks += n
        print(f"  {storage_path}: {n} chunks")

    print(f"\n{len(pending)} documents chunked, {total_chunks} total chunks")


if __name__ == "__main__":
    run()
