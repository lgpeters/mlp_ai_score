"""One-off third pass: for every SEC filing chunk already tagged
ai_category=5 (Clear Negative), check whether the text is generic,
evergreen risk-factor boilerplate (the kind of language that appears in
essentially every tech company's 10-K every year -- competition,
geopolitical/supply-chain risk, foundry dependency, licensing) rather
than a genuine, specific, timely negative development. Writes a single
boolean, is_boilerplate_risk, back to chunk_classifications.

No company-specific context needed -- the question ("is this boilerplate
risk language") is the same regardless of ticker, so this uses ONE shared
cached system prompt for every chunk, not one per company like the
second pass.
"""

import os
import sys
from pathlib import Path

from anthropic import Anthropic
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from shared.db import get_connection
from shared.storage import get_chunk_content

load_dotenv()

MODEL = "claude-sonnet-5"
GROUP_SIZE = 8

SYSTEM_PROMPT = """You are checking whether a text excerpt is generic, evergreen SEC-filing
risk-factor boilerplate, or a genuine, specific, timely negative development.

Every excerpt below has already been classified as "Clear Negative" AI-relevant content
from a company's SEC filing (10-K/10-Q/20-F/6-K).

Boilerplate risk language (is_boilerplate_risk = true):
- Generic competitive-landscape statements (e.g. "we compete with X, Y, Z who may develop
  similar AI technology") that would read almost identically in any given year's filing.
- Generic geopolitical/supply-chain risk framing (e.g. Taiwan/China tensions, export
  controls, tariffs) stated as a standing risk factor, not tied to a specific new event.
- Generic dependency statements (foundries, third-party licensors, subcontractors) framed
  as an ongoing structural risk rather than a specific recent development.
- Standard legal/regulatory boilerplate mentioning AI only in passing as one of many listed
  risk categories.

NOT boilerplate (is_boilerplate_risk = false):
- A specific negative event, number, or admission (e.g. a stated revenue decline, a
  concrete competitive loss, an admission of falling behind on a specific product).
- Language tied to a specific time period, product, or metric rather than a generic,
  repeatable risk-factor template.
- Forward-looking commentary that is specific to the company's actual current situation,
  not interchangeable boilerplate.

Each request contains multiple numbered items (Item 0, Item 1, ...). Use the
classify_boilerplate tool to return one result per input item, in the same order, each
tagged with its index."""

_TOOL_SCHEMA = {
    "name": "classify_boilerplate",
    "description": "Return is_boilerplate_risk for each input item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "is_boilerplate_risk": {"type": "boolean"},
                    },
                    "required": ["index", "is_boilerplate_risk"],
                },
            },
        },
        "required": ["results"],
    },
}

PENDING_SQL = """
    select ci.chunk_id, ci.content_id, ci.start_offset, ci.end_offset, c.storage_path
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    where c.type = 'sec_filing' and cc.ai_category = 5
      and cc.is_boilerplate_risk is null and cc.batch_id is null
"""

TRACK_REQUEST_SQL = "insert into chunk_batch_requests (batch_id, custom_id, chunk_ids) values (%s, %s, %s)"
MARK_SUBMITTED_SQL = "update chunk_classifications set batch_id = %s where chunk_id = any(%s)"
WRITE_RESULT_SQL = "update chunk_classifications set is_boilerplate_risk = %s, batch_id = null where chunk_id = %s"
CLEAR_BATCH_SQL = "update chunk_classifications set batch_id = null where chunk_id = any(%s)"


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def get_pending() -> list[tuple]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL)
            return cur.fetchall()


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _build_request(custom_id: str, group: list[tuple]) -> Request:
    lines = []
    for i, (chunk_id, content_id, start, end, storage_path) in enumerate(group):
        text = get_chunk_content(content_id, "sec_filing", storage_path, start, end)
        lines.append(f"Item {i}:\n{text}")
    return Request(
        custom_id=custom_id,
        params={
            "model": MODEL,
            "max_tokens": 1024,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            "messages": [{"role": "user", "content": "\n\n".join(lines)}],
            "tools": [_TOOL_SCHEMA],
            "tool_choice": {"type": "tool", "name": "classify_boilerplate"},
        },
    )


def _prewarm_cache() -> None:
    _client().messages.create(
        model=MODEL, max_tokens=0,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        messages=[{"role": "user", "content": "warmup"}],
    )


def submit_batch() -> str:
    pending = get_pending()
    if not pending:
        print("nothing pending")
        return ""

    _prewarm_cache()
    print(f"pre-warmed cache; {len(pending)} chunks pending")

    groups = _chunk(pending, GROUP_SIZE)
    requests = []
    group_chunk_ids = []
    for i, group in enumerate(groups):
        custom_id = f"boilerplate-{i}"
        requests.append(_build_request(custom_id, group))
        group_chunk_ids.append((custom_id, [row[0] for row in group]))

    batch = _client().messages.batches.create(requests=requests)
    all_chunk_ids = [cid for _, cids in group_chunk_ids for cid in cids]

    with get_connection() as conn:
        with conn.cursor() as cur:
            for custom_id, chunk_ids in group_chunk_ids:
                cur.execute(TRACK_REQUEST_SQL, (batch.id, custom_id, chunk_ids))
            cur.execute(MARK_SUBMITTED_SQL, (batch.id, all_chunk_ids))

    print(f"submitted batch {batch.id}: {len(requests)} requests, {len(all_chunk_ids)} chunks")
    return batch.id


def fetch_results(batch_id: str) -> None:
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"batch {batch_id} still processing (status: {batch.processing_status})")
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select custom_id, chunk_ids from chunk_batch_requests where batch_id = %s", (batch_id,))
            chunk_ids_by_custom_id = dict(cur.fetchall())

    scored = 0
    failed = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for entry in client.messages.batches.results(batch_id):
                chunk_ids = chunk_ids_by_custom_id.get(entry.custom_id, [])
                if entry.result.type != "succeeded":
                    cur.execute(CLEAR_BATCH_SQL, (chunk_ids,))
                    failed += len(chunk_ids)
                    continue

                tool_use = next((b for b in entry.result.message.content if b.type == "tool_use"), None)
                if tool_use is None:
                    cur.execute(CLEAR_BATCH_SQL, (chunk_ids,))
                    failed += len(chunk_ids)
                    continue

                results = tool_use.input["results"]
                indices = sorted(item["index"] for item in results)
                if indices != list(range(len(chunk_ids))):
                    print(f"  {entry.custom_id}: index mismatch, leaving for retry")
                    cur.execute(CLEAR_BATCH_SQL, (chunk_ids,))
                    failed += len(chunk_ids)
                    continue

                for item in results:
                    cur.execute(WRITE_RESULT_SQL, (item["is_boilerplate_risk"], chunk_ids[item["index"]]))
                    scored += 1

    print(f"batch {batch_id}: {scored} scored, {failed} failed (cleared for retry)")


if __name__ == "__main__":
    submit_batch()
