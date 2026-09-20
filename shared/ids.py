import uuid

# Fixed namespace for this project's deterministic content IDs.
CONTENT_ID_NAMESPACE = uuid.UUID("6f6b4f2e-6d43-4f0a-9c8a-2e9b7f1a5c31")


def content_id_for(data_source: str, source_doc_id: str) -> uuid.UUID:
    """Deterministic content_id derived from (data_source, source_doc_id).

    Same inputs always produce the same UUID, so callers can compute a
    content row's id up front and reuse it for related rows (sec_filings,
    chunks, ...) in the same batch, before the content row is inserted.
    """
    return uuid.uuid5(CONTENT_ID_NAMESPACE, f"{data_source}:{source_doc_id}")
