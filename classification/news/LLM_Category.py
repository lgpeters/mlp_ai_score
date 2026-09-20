"""SMARTER SECOND PASS -- scores category (1-6), materiality (0-10), and
saliency (-3 to 3) on chunks that already cleared the cheap first pass
(classification/news/LLM_Filtering.py): company_relevant = true AND
ai_associated > 0. No further filtering or retrieval happens here -- every
chunk that passed the gate gets classified, since the whole point of the
two-pass split was to make sure the expensive model only ever sees rows
already known to be worth it.

Uses a genuinely smarter/pricier model (Sonnet, not Haiku) and a much
longer system prompt (the full 6-category taxonomy plus materiality/
saliency scoring guidance) than the first pass -- long enough that it
should actually clear Sonnet's ~1024-token prompt-caching floor, unlike
the first pass's short prompt on Haiku's ~2048-token floor.

Two-phase, same pattern as LLM_Filtering.py:
  - submit_batch(): groups every pending chunk, submits them as one
    Anthropic Batch, marks them in-flight.
  - fetch_results(batch_id): call once the batch has finished; writes
    ai_category/ai_materiality_score/ai_saliency_score. Does NOT touch
    company_relevant/ai_associated/round -- those already reflect the
    first pass and this only ever runs on rows where they're already set.

NOT YET RUN -- requires ANTHROPIC_API_KEY in .env. Scoped to Hacker News
only for now (classification/ClassDescriptions.md's taxonomy applied to
sec/transcripts is a later extension, not yet built).
"""

import os
from pathlib import Path

from anthropic import Anthropic
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv

from shared.db import get_connection

load_dotenv()

MODEL = "claude-sonnet-5"
# Titles are short, so pack many per request -- the point of grouping here
# is purely to amortize the (much longer, and this time likely genuinely
# cached) system prompt across more content, not a correctness tradeoff
# the way small GROUP_SIZE was for the first pass's forced-tool-use risk.
GROUP_SIZE = 20

SYSTEM_PROMPT = """You are doing the SECOND, more careful pass on news headlines that a cheap \
first-pass model already confirmed are genuinely about the named company and genuinely \
AI-associated. Your job is NOT to re-decide relevance -- both of those gates have already \
been passed. Your job is to categorize the AI content and judge how much it actually matters.

For each item you are given a company name, a publication date, and a headline. Score each \
one on three axes:

1. ai_category (integer 1-6): which of the following the headline's AI content best fits.
   1 = AI Partnership -- a clear announcement or positive comment on an acquisition, M&A deal, \
or partnership with an LLM infra/compute provider. Materiality to the core business is likely \
unclear.
   2 = Operational Efficiencies -- clear commentary that adoption or internal utilisation of AI \
is generating procedural efficiencies within the business, increased margins, improved \
procedural velocity.
   3 = Restructuring -- an indication that AI is impacting the business' organisational chart, \
headcount, hiring, or salary costs.
   4 = Clear Positive -- a clear statement of positive exposure to AI, e.g. AI-driven revenue, \
demand, or improved margins in a company's end-markets.
   5 = Clear Negative -- a clear statement of negative exposure to AI, e.g. budgets displaced \
by AI spending, AI eroding a company's end-market, threats from new entrants, or AI indicated \
as a direct risk to a product category.
   6 = Product Roadmap -- a statement indicating the business is releasing genuinely novel \
product categories or engaging in genuine R&D to implement AI into its goods/services.
   If more than one category plausibly applies, pick whichever is the PRIMARY point of the \
headline -- the thing a reader would say the headline is actually about, not a secondary detail.

2. ai_materiality_score (integer 0-10): how material this is to the company's core business, \
within whatever category you picked. 0 means purely speculative, rumored, or trivial in scale \
(a minor pilot, a single small partnership, a passing mention). 10 means unambiguously \
significant to the company's overall business (a major strategic shift, a deal or product line \
large enough to move the business as a whole). Judge scale and concreteness, not tone -- a \
confidently-worded press release about a tiny pilot program is still low materiality.

3. ai_saliency_score (integer -3 to 3): the DIRECTION of business impact implied, not the tone \
of the writing. Positive means the AI development plausibly helps the company's business \
(revenue, margin, competitive position, strategic optionality). Negative means it plausibly \
hurts the business (cost, displacement, competitive threat, execution risk). 0 means genuinely \
neutral or impact-direction is not inferable from the headline alone (e.g. a purely descriptive \
product-launch headline with no stated business consequence). Do not infer saliency from \
enthusiasm or negativity in the writing style -- an excitedly-written headline about a risk is \
still negative saliency, and a flatly-written headline about a real win is still positive.

Use the classify_categories tool to return one result per input item, in the same order, each \
tagged with its index."""

_TOOL_USE_INSTRUCTION = ""  # already covered in SYSTEM_PROMPT above


def _tool_schema(n: int) -> dict:
    return {
        "name": "classify_categories",
        "description": "Return category/materiality/saliency for each input item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "minItems": n,
                    "maxItems": n,
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {"type": "integer", "description": "0-based index of the item in the input list"},
                            "ai_category": {"type": "integer", "enum": [1, 2, 3, 4, 5, 6]},
                            "ai_materiality_score": {"type": "integer", "minimum": 0, "maximum": 10},
                            "ai_saliency_score": {"type": "integer", "minimum": -3, "maximum": 3},
                        },
                        "required": ["index", "ai_category", "ai_materiality_score", "ai_saliency_score"],
                    },
                },
            },
            "required": ["results"],
        },
    }


def _validate_results(results: list[dict], n: int) -> bool:
    """True only if indices are exactly {0, ..., n-1} -- no missing,
    duplicate, or out-of-range entries."""
    indices = [item["index"] for item in results]
    return sorted(indices) == list(range(n))


# Scoped to hackernews only -- see module docstring. Only rows that
# already passed the cheap gate (company_relevant AND ai_associated) and
# haven't been through this pass yet (ai_category is null).
PENDING_SQL = """
    select ci.chunk_id, ci.content_id, ci.start_offset, ci.end_offset,
           h.title, co.name as company_name, c.publication_date
    from content_index ci
    join content c using (content_id)
    join companies co using (company_id)
    join lateral (
        select title from hackernews h2 where h2.content_id = c.content_id order by h2.story_id limit 1
    ) h on true
    left join chunk_classifications cc using (chunk_id)
    where c.type = 'hackernews'
      and cc.company_relevant = true and cc.ai_associated > 0
      and cc.ai_category is null and cc.batch_id is null
"""

MARK_SUBMITTED_SQL = "update chunk_classifications set batch_id = %s where chunk_id = any(%s)"

TRACK_REQUEST_SQL = """
    insert into chunk_batch_requests (batch_id, custom_id, chunk_ids)
    values (%s, %s, %s)
"""

WRITE_SCORE_SQL = """
    update chunk_classifications
    set ai_category = %s, ai_materiality_score = %s, ai_saliency_score = %s,
        scored_at = now(), batch_id = null
    where chunk_id = %s
"""

CLEAR_BATCH_SQL = "update chunk_classifications set batch_id = null where chunk_id = any(%s)"

RECORD_USAGE_SQL = """
    update chunk_batch_requests set input_tokens = %s, output_tokens = %s, model = %s
    where batch_id = %s and custom_id = %s
"""


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def get_pending() -> list[tuple]:
    """Returns [(chunk_id, content_id, start_offset, end_offset, title, company_name, publication_date), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL)
            return cur.fetchall()


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _build_request(custom_id: str, group: list[tuple]) -> Request:
    lines = []
    for i, (chunk_id, content_id, start, end, title, company_name, pub_date) in enumerate(group):
        date_str = pub_date.date().isoformat() if pub_date else "unknown date"
        lines.append(f"Item {i}:\n{company_name}\nDate: {date_str}\n{title[start:end]}")

    return Request(
        custom_id=custom_id,
        params={
            "model": MODEL,
            "max_tokens": 2048,
            "system": [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            ],
            "messages": [{"role": "user", "content": "\n\n".join(lines)}],
            "tools": [_tool_schema(len(group))],
            "tool_choice": {"type": "tool", "name": "classify_categories"},
        },
    )


def submit_batch(limit_groups: int | None = None) -> str:
    """Groups every pending chunk and submits them as one Anthropic Batch."""
    pending = get_pending()
    groups = _chunk(pending, GROUP_SIZE)
    if limit_groups is not None:
        groups = groups[:limit_groups]

    if not groups:
        print("nothing pending")
        return ""

    requests = []
    group_chunk_ids = []
    for i, group in enumerate(groups):
        custom_id = f"group-{i}"
        requests.append(_build_request(custom_id, group))
        group_chunk_ids.append((custom_id, [row[0] for row in group]))

    batch = _client().messages.batches.create(requests=requests)
    all_chunk_ids = [chunk_id for _, chunk_ids in group_chunk_ids for chunk_id in chunk_ids]

    with get_connection() as conn:
        with conn.cursor() as cur:
            for custom_id, chunk_ids in group_chunk_ids:
                cur.execute(TRACK_REQUEST_SQL, (batch.id, custom_id, chunk_ids))
            cur.execute(MARK_SUBMITTED_SQL, (batch.id, all_chunk_ids))

    print(f"submitted batch {batch.id}: {len(requests)} requests, {sum(len(g) for g in groups)} chunks")
    return batch.id


def fetch_results(batch_id: str) -> None:
    """Call once the batch has finished. Safe to call before that -- just
    reports it's still processing."""
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
                    print(f"  {entry.custom_id}: {entry.result.type}, leaving for retry")
                    cur.execute(CLEAR_BATCH_SQL, (chunk_ids,))
                    failed += len(chunk_ids)
                    continue

                usage = entry.result.message.usage
                cur.execute(RECORD_USAGE_SQL, (
                    usage.input_tokens, usage.output_tokens, entry.result.message.model,
                    batch_id, entry.custom_id,
                ))

                tool_use = next((b for b in entry.result.message.content if b.type == "tool_use"), None)
                if tool_use is None:
                    print(f"  {entry.custom_id}: no tool_use block, leaving for retry")
                    cur.execute(CLEAR_BATCH_SQL, (chunk_ids,))
                    failed += len(chunk_ids)
                    continue

                results = tool_use.input["results"]
                if not _validate_results(results, len(chunk_ids)):
                    got = sorted(item["index"] for item in results)
                    print(f"  {entry.custom_id}: index mismatch (expected 0-{len(chunk_ids)-1}, got {got}), leaving for retry")
                    cur.execute(CLEAR_BATCH_SQL, (chunk_ids,))
                    failed += len(chunk_ids)
                    continue

                for item in results:
                    cur.execute(WRITE_SCORE_SQL, (
                        item["ai_category"], item["ai_materiality_score"], item["ai_saliency_score"],
                        chunk_ids[item["index"]],
                    ))
                    scored += 1

    print(f"batch {batch_id}: {scored} scored, {failed} failed (cleared for retry)")


if __name__ == "__main__":
    submit_batch()
