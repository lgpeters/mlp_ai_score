"""CHEAP FIRST PASS ONLY -- scores company relevance and whether there's any
genuine AI association at all (company_relevant, ai_associated), using a
small/fast model. Category (1-4) and materiality/saliency are deliberately
NOT scored here -- that's a second pass, run only on rows where both this
pass's fields are true, using a smarter (more expensive) model. The whole
point of this split is to never spend the expensive judgment on rows this
cheap gate already rules out.

Operates on content_index chunks (chunk_classifications), not on any one
source table directly -- the same pipeline covers SEC filings, transcripts,
and Hacker News chunks alike, mirroring classification/vader_scoring.py's
generic approach.

Uses the Anthropic Batch API (50% cheaper than synchronous calls) with
prompt caching on the system prompt (1h TTL, to stay warm across a batch's
processing window, which can take longer than the default 5-minute cache
lifetime).

Two-phase, since batches are async and can take a while to complete:
  - submit_batches(): groups pending chunks, submits them, marks them
    in-flight (chunk_classifications.batch_id) so a rerun doesn't resubmit
    them.
  - fetch_results(batch_id): call this later (once the batch has finished)
    to pull results and write scores. Safe to call repeatedly -- a batch
    that isn't done yet is just reported as still processing.

NOT YET RUN -- requires ANTHROPIC_API_KEY in .env.
"""

import os
from pathlib import Path

from anthropic import Anthropic
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv

from shared.db import get_connection

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
# Most of the cost benefit of grouping comes from prompt caching (~90%
# cheaper cached reads), not the grouping itself -- so group size is mainly
# a correctness/risk tradeoff now, not a cost one. Kept small to limit the
# blast radius of a misaligned response (see _validate_results). Also
# note: classification_prompt.md is well under Haiku's ~2048-token minimum
# cacheable prompt length, so cache_control below is likely a no-op in
# practice -- harmless either way, and irrelevant to cost at this scale.
GROUP_SIZE = 8

PROMPT_PATH = Path(__file__).resolve().parent / "classification_prompt.md"

# Kept as documentation for the second (smarter-model) pass, which scores
# these 6 categories plus materiality/saliency -- NOT scored by this cheap
# first pass. No "no association" category here, since that's now what
# ai_associated=0 in THIS pass already means. See classification/
# ClassDescriptions.md for the canonical ground-truth definitions.
CATEGORY_TAXONOMY_FOR_SECOND_PASS = """
1: AI Partnership -- a clear announcement or positive comment on an acquisition, M&A deal, or
   partnership with an LLM infra/compute provider. Materiality to the core business is likely
   unclear.
2: Operational Efficiencies -- clear commentary that adoption or internal utilisation of AI is
   generating procedural efficiencies within the business, increased margins, improved
   procedural velocity.
3: Restructuring -- an indication that AI is impacting the business' organisational chart,
   headcount, hiring, or salary costs.
4: Clear Positive -- a clear statement of positive exposure to AI, e.g. AI-driven revenue,
   demand, or improved margins in a company's end-markets.
5: Clear Negative -- a clear statement of negative exposure to AI, e.g. budgets displaced by AI
   spending, AI eroding a company's end-market, threats from new entrants, or AI indicated as a
   direct risk to a product category.
6: Product Roadmap -- a statement indicating the business is releasing genuinely novel product
   categories or engaging in genuine R&D to implement AI into its goods/services.
"""

# Loaded verbatim from the finalized, user-authored prompt -- the canonical
# definition of company_relevant/ai_associated for this cheap first pass,
# including the worked examples that fixed real disagreements found during
# manual benchmarking (classification/news/golden_news.csv). Edit that file,
# not this one, to change scoring behavior.
_TOOL_USE_INSTRUCTION = (
    "\n\nEach request contains multiple numbered items (Item 0, Item 1, ...). Use the "
    "classify_chunks tool to return one result per input item, in the same order, each "
    "tagged with its index."
)
SYSTEM_PROMPT = PROMPT_PATH.read_text() + _TOOL_USE_INSTRUCTION


def _tool_schema(n: int) -> dict:
    """n = exact number of input items in this request -- constrains the
    array length in the schema itself (necessary but not sufficient; see
    _validate_results for the check schema alone can't express: that the
    returned indices are exactly {0, ..., n-1} with no duplicates)."""
    return {
        "name": "classify_chunks",
        "description": "Return company-relevance and AI-association gates for each input chunk.",
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
                            "company_relevant": {"type": "boolean"},
                            "ai_associated": {"type": "integer", "enum": [0, 1, 2, 3]},
                        },
                        "required": ["index", "company_relevant", "ai_associated"],
                    },
                },
            },
            "required": ["results"],
        },
    }


def _validate_results(results: list[dict], n: int) -> bool:
    """True only if indices are exactly {0, ..., n-1} -- no missing,
    duplicate, or out-of-range entries. Schema constraints (minItems/
    maxItems) catch the count; this catches everything schema can't."""
    indices = [item["index"] for item in results]
    return sorted(indices) == list(range(n))


# Scoped to hackernews only -- classification_prompt.md's format
# (<Company Name>/<News Title>) is written for HN titles specifically.
# SEC filings and transcripts are paragraph-length chunks, not single
# titles, and would need their own prompt/module.
#
# h.title is joined in directly here (rather than looked up per-chunk via
# get_chunk_content/get_source_text) specifically to avoid an N+1 query
# pattern -- 5000+ chunks means 5000+ individual round-trips (each opening
# its own connection) if done per-row, which is what made the first
# real-data run of this pipeline take 6+ minutes just to build requests,
# before ever reaching the Anthropic API call.
PENDING_SQL = """
    select ci.chunk_id, ci.content_id, ci.start_offset, ci.end_offset,
           h.title, co.name as company_name
    from content_index ci
    join content c using (content_id)
    join companies co using (company_id)
    -- LATERAL + limit 1: a content_id can legitimately have more than one
    -- hackernews row sharing it (the same story resubmitted under the same
    -- ticker with a different story_id -- a repost dedup_titles() doesn't
    -- always fully collapse) -- a plain join would fan that out into
    -- duplicate pending rows. There's exactly one content_index/content
    -- row per content_id regardless, so pick one title deterministically,
    -- same as get_source_text's arbitrary-but-single pick for hackernews.
    join lateral (
        select title from hackernews h2 where h2.content_id = c.content_id order by h2.story_id limit 1
    ) h on true
    left join chunk_classifications cc using (chunk_id)
    where c.type = 'hackernews'
      and (cc.chunk_id is null or (cc.ai_associated is null and cc.batch_id is null))
    order by ci.content_id, ci.start_offset
"""

# Bulk upsert over every pending chunk_id at once (all sharing the same
# batch_id, since they're all part of the same Anthropic Batch) -- one
# round-trip instead of one per chunk.
MARK_SUBMITTED_SQL = """
    insert into chunk_classifications (chunk_id, batch_id)
    select x, %s from unnest(%s::text[]) as x
    on conflict (chunk_id) do update set batch_id = excluded.batch_id
"""

TRACK_REQUEST_SQL = """
    insert into chunk_batch_requests (batch_id, custom_id, chunk_ids)
    values (%s, %s, %s)
"""

WRITE_SCORE_SQL = """
    insert into chunk_classifications (chunk_id, company_relevant, ai_associated, scored_at, batch_id)
    values (%s, %s, %s, now(), null)
    on conflict (chunk_id) do update set
        company_relevant = excluded.company_relevant,
        ai_associated = excluded.ai_associated,
        scored_at = excluded.scored_at,
        batch_id = null
"""

CLEAR_BATCH_SQL = "update chunk_classifications set batch_id = null where chunk_id = any(%s)"

RECORD_USAGE_SQL = """
    update chunk_batch_requests set input_tokens = %s, output_tokens = %s, model = %s
    where batch_id = %s and custom_id = %s
"""


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def get_pending() -> list[tuple]:
    """Returns [(chunk_id, content_id, start_offset, end_offset, title, company_name), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL)
            return cur.fetchall()


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _build_request(custom_id: str, group: list[tuple]) -> Request:
    # Matches classification_prompt.md's stated format exactly:
    #     <Company Name>
    #     <News Title>
    # with an "Item N:" header per entry so multiple items can share one
    # request while still being unambiguously indexable in the response.
    # Title is sliced locally (start/end offset, mirroring content_index's
    # chunk boundaries) -- no per-row DB call, see PENDING_SQL's comment.
    lines = []
    for i, (chunk_id, content_id, start, end, title, company_name) in enumerate(group):
        lines.append(f"Item {i}:\n{company_name}\n{title[start:end]}")

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
            "tool_choice": {"type": "tool", "name": "classify_chunks"},
        },
    )


def submit_batches(limit_groups: int | None = None) -> str:
    """Groups pending chunks, submits them as one Anthropic Batch, and marks
    every included row with the batch_id so reruns don't resubmit them."""
    pending = get_pending()
    groups = _chunk(pending, GROUP_SIZE)
    if limit_groups is not None:
        groups = groups[:limit_groups]

    requests = []
    group_chunk_ids = []
    for i, group in enumerate(groups):
        custom_id = f"group-{i}"
        requests.append(_build_request(custom_id, group))
        group_chunk_ids.append((custom_id, [row[0] for row in group]))

    if not requests:
        print("nothing pending")
        return ""

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
    """Call once the batch has finished (check the Anthropic dashboard, or
    client.messages.batches.retrieve(batch_id).processing_status == 'ended').
    Safe to call before that -- just reports it's still processing."""
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

                # Recorded unconditionally for every succeeded result, even
                # if the tool_use/validation checks below end up rejecting
                # it -- a wasted call still cost real tokens.
                usage = entry.result.message.usage
                cur.execute(RECORD_USAGE_SQL, (
                    usage.input_tokens, usage.output_tokens, entry.result.message.model,
                    batch_id, entry.custom_id,
                ))

                tool_use = next(
                    (b for b in entry.result.message.content if b.type == "tool_use"), None
                )
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
                        chunk_ids[item["index"]],
                        item["company_relevant"], item["ai_associated"],
                    ))
                    scored += 1

    print(f"batch {batch_id}: {scored} scored, {failed} failed (cleared for retry)")


if __name__ == "__main__":
    submit_batches()
