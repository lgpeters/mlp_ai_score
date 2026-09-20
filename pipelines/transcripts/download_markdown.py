import hashlib
import json
import re
import time

import requests

from shared.db import get_connection
from shared.storage import upload_text

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DATA_SOURCE = "stockanalysis.com"

# Polite delay between requests -- rapid sequential requests without one
# triggered 403s from stockanalysis.com's bot protection.
REQUEST_DELAY_SECONDS = 1.5

# Below this, treat the response as broken rather than a genuine transcript --
# catches empty bodies, error/placeholder pages, and other silent failures.
# Real earnings call transcripts are tens of thousands of characters; this
# threshold is well below the smallest genuine one.
MIN_MARKDOWN_LENGTH = 500

# The transcript body is embedded as a JS (not strict JSON) object literal
# inside a <script> tag, not in the visible HTML -- unquoted keys, so pulled
# out with regex rather than a full parse. Each speaker block follows this
# exact field order: role, company (both may be empty, e.g. for the
# operator), then its paragraphs, then the speaker's name last.
SPEAKER_BLOCK_RE = re.compile(
    r'role:"((?:[^"\\]|\\.)*)",company:"((?:[^"\\]|\\.)*)",paragraphs:'
    r'|text:"((?:[^"\\]|\\.)*)"'
    r'|speakerName:"((?:[^"\\]|\\.)*)"'
)

# Legacy page format used by some older transcripts: the whole body as one
# flat string instead of structured per-speaker blocks. No speaker
# attribution available in this format, so it's used as-is (plain text).
FULL_BODY_RE = re.compile(r'fullTranscriptBody:"((?:[^"\\]|\\.)*)"')


class DownloadError(Exception):
    pass


PENDING_SQL = """
    select c.content_id, t.url, co.ticker
    from content c
    join transcripts t using (content_id)
    join companies co using (company_id)
    where c.data_source = %(data_source)s and c.storage_path is null
    order by c.publication_date desc
"""

MARK_DOWNLOADED_SQL = """
    update content
    set storage_path = %s, content_hash = %s, downloaded_at = now()
    where content_id = %s
"""


def get_pending() -> list[tuple[str, str, str]]:
    """Returns [(content_id, url, ticker), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL, {"data_source": DATA_SOURCE})
            return cur.fetchall()


def _unescape(js_string: str) -> str:
    return json.loads(f'"{js_string}"')


def _extract_markdown(html: str) -> str:
    sections = []
    pending_role = None
    pending_company = None
    buffer: list[str] = []

    for match in SPEAKER_BLOCK_RE.finditer(html):
        role, company, text, speaker = match.groups()
        if role is not None:
            pending_role, pending_company = _unescape(role), _unescape(company)
        elif text is not None:
            buffer.append(_unescape(text))
        elif speaker is not None:
            sections.append((_unescape(speaker), pending_role, pending_company, buffer))
            buffer = []
            pending_role = pending_company = None

    parts = []
    for speaker, role, company, paragraphs in sections:
        heading = speaker
        if role:
            heading += f" ({role}{f', {company}' if company else ''})"
        parts.append(f"### {heading}\n\n" + "\n\n".join(paragraphs))

    if parts:
        return "\n\n".join(parts)

    # Fall back to the legacy flat-body format if no structured speaker
    # blocks were found.
    fallback = FULL_BODY_RE.search(html)
    return _unescape(fallback.group(1)) if fallback else ""


def download_one(content_id: str, url: str, ticker: str) -> None:
    try:
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
    except Exception as e:
        raise DownloadError(f"fetch failed: {e}") from e

    try:
        md = _extract_markdown(resp.text)
    except Exception as e:
        raise DownloadError(f"markdown extraction failed: {e}") from e

    if not md or len(md.strip()) < MIN_MARKDOWN_LENGTH:
        actual_len = len(md.strip()) if md else 0
        raise DownloadError(f"content too short to be genuine ({actual_len} chars, expected >= {MIN_MARKDOWN_LENGTH})")

    content_hash = hashlib.sha256(md.encode("utf-8")).hexdigest()
    content_item_id = content_id.hex if hasattr(content_id, "hex") else content_id
    storage_path = f"transcripts/{ticker}/{content_item_id}.md"
    upload_text(storage_path, md)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(MARK_DOWNLOADED_SQL, (storage_path, content_hash, content_id))

    print(f"  ok    {ticker} {url} -> {storage_path} ({len(md):,} chars)")


def run(limit: int | None = None) -> None:
    pending = get_pending()
    if limit is not None:
        pending = pending[:limit]

    print(f"{len(pending)} transcripts to download")

    failures = []
    for i, (content_id, url, ticker) in enumerate(pending):
        try:
            download_one(content_id, url, ticker)
        except DownloadError as e:
            print(f"  FAIL  {url}: {e}")
            failures.append((url, str(e)))

        if i < len(pending) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\n{len(pending) - len(failures)} succeeded, {len(failures)} failed")
    if failures:
        print("failed urls (left as storage_path=null, safe to retry):")
        for url, reason in failures:
            print(f"  {url}: {reason}")


if __name__ == "__main__":
    run()
