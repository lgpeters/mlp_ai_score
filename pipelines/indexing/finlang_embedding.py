from itertools import groupby

from sentence_transformers import SentenceTransformer

from shared.db import get_connection
from shared.storage import download_text

MODEL_NAME = "FinLang/finance-embeddings-investopedia"

PENDING_SQL = """
    select ci.chunk_id, ci.content_id, ci.start_offset, ci.end_offset, c.storage_path
    from content_index ci
    join content c using (content_id)
    where ci.finlang_embedding is null
    order by ci.content_id
"""

UPDATE_SQL = "update content_index set finlang_embedding = %s::halfvec where chunk_id = %s"

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _to_pgvector_literal(vec) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def get_pending() -> list[tuple[str, str, int, int, str]]:
    """Returns [(chunk_id, content_id, start_offset, end_offset, storage_path), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL)
            return cur.fetchall()


def embed_document(content_id: str, rows: list[tuple[str, str, int, int, str]]) -> int:
    storage_path = rows[0][4]
    text = download_text(storage_path)

    chunk_ids = [r[0] for r in rows]
    chunk_texts = [text[r[2]:r[3]] for r in rows]

    embeddings = get_model().encode(chunk_texts)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                UPDATE_SQL,
                [(_to_pgvector_literal(vec), chunk_id) for chunk_id, vec in zip(chunk_ids, embeddings)],
            )

    return len(rows)


def run(limit_documents: int | None = None) -> None:
    pending = get_pending()
    by_content_id = [(cid, list(group)) for cid, group in groupby(pending, key=lambda r: r[1])]

    if limit_documents is not None:
        by_content_id = by_content_id[:limit_documents]

    print(f"{len(by_content_id)} documents ({sum(len(g) for _, g in by_content_id)} chunks) to embed")

    total = 0
    for content_id, rows in by_content_id:
        n = embed_document(content_id, rows)
        total += n
        print(f"  {content_id}: {n} chunks embedded")

    print(f"\n{len(by_content_id)} documents, {total} chunks embedded")


if __name__ == "__main__":
    run()
