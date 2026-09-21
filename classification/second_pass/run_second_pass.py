"""SMARTER SECOND PASS -- category (1-6), materiality (1-10), and saliency
(-3 to 3) for every chunk that already cleared the cheap first pass
(company_relevant = true AND ai_associated > 0). No further filtering or
retrieval -- every qualifying chunk gets classified, since the whole
point of the two-pass split was to make sure this smarter, pricier model
only ever sees rows already known to be worth it.

Iterates over (company x content_type). The system prompt is built fresh
per pair and cached (Anthropic prompt caching, 1h TTL) -- it's the same
for every chunk belonging to that company and that content type, but
different companies/content types get genuinely different prompts (a
company-specific business description changes the materiality judgment),
so caching is scoped per-pair, not global:

    SYSTEM_PROMPT[company, content_type] =
        second_pass_prompts/SecondPassInstructions.md
      + second_pass_prompts/ContentFrontMatter/{content_type}.md
      + "You are assessing for company: {company.name}"
      + second_pass_prompts/CompanyDescriptions/{ticker}.md

CONTENT_TYPES maps a front-matter label (matching a
ContentFrontMatter/*.md filename) to the actual content.type value it
scopes to in the database. Only "News" -> "hackernews" exists right now;
adding SEC/transcripts later is just dropping in another front-matter
file and adding one line here.

Every (company, content_type) pair's requests go into ONE single
Anthropic Batch call -- not one batch per pair -- since the whole point
of "spam every qualifying chunk" is to not run this repeatedly.

NOT YET RUN -- requires ANTHROPIC_API_KEY in .env, and every
CompanyDescriptions/{ticker}.md to be filled in (most are still empty
placeholders as of writing this).
"""

import os
from pathlib import Path

from anthropic import Anthropic
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv

from shared.db import get_connection
from shared.storage import get_chunk_content

load_dotenv()

MODEL = "claude-sonnet-5"

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "second_pass_prompts"
INSTRUCTIONS = (PROMPTS_DIR / "SecondPassInstructions.md").read_text()

# front-matter label -> {content.type, optional sub_type filter, group
# size}. Add an entry here (and a matching ContentFrontMatter/<Label>.md)
# to extend to a new source -- everything else in this module is already
# generic. sub_types=None means every sub_type of that content.type
# qualifies (e.g. SEC covers 10-K/10-Q/20-F/6-K together); a list scopes
# to just those (e.g. Transcripts is earnings_call only, per an explicit
# scope decision -- "other" transcripts are deliberately excluded here
# even though they also have qualifying chunks).
#
# group_size is smaller for SEC/Transcripts than News: their chunks are
# full paragraphs, not short titles, so packing 20 of them into one
# request the way News does would make for a much bigger, riskier
# request (more to get right in one forced-tool-use call, bigger output).
CONTENT_TYPES = {
    "News": {"type": "hackernews", "sub_types": None, "group_size": 20},
    "SEC": {"type": "sec_filing", "sub_types": None, "group_size": 8},
    "Transcripts": {"type": "transcripts", "sub_types": ["earnings_call"], "group_size": 8},
}

_TOOL_USE_INSTRUCTION = (
    "\n\nEach request contains multiple numbered items (Item 0, Item 1, ...). Use the "
    "classify_second_pass tool to return one result per input item, in the same order, "
    "each tagged with its index."
)


def _system_prompt(content_label: str, ticker: str, company_name: str) -> str:
    front_matter = (PROMPTS_DIR / "ContentFrontMatter" / f"{content_label}.md").read_text()
    company_desc = (PROMPTS_DIR / "CompanyDescriptions" / f"{ticker}.md").read_text().strip()
    if not company_desc:
        raise ValueError(f"CompanyDescriptions/{ticker}.md is empty -- fill it in before running")

    return (
        INSTRUCTIONS
        + "\n\n"
        + front_matter
        + f"\n\nYou are assessing for company:\n{company_name}\n\n"
        + "The business description:\n"
        + company_desc
        + _TOOL_USE_INSTRUCTION
    )


def _tool_schema(n: int) -> dict:
    return {
        "name": "classify_second_pass",
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
                            "ai_materiality_score": {"type": "integer", "minimum": 1, "maximum": 10},
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
    indices = [item["index"] for item in results]
    return sorted(indices) == list(range(n))


# Only rows that already passed the cheap gate and haven't been through
# this pass yet, for one specific (company, content_type) pair.
# sub_types::text[] is null matches everything (News/SEC); a real array
# (Transcripts) restricts to just those sub_types.
PENDING_SQL = """
    select ci.chunk_id, ci.content_id, ci.start_offset, ci.end_offset, c.storage_path
    from content_index ci
    join content c using (content_id)
    left join chunk_classifications cc using (chunk_id)
    where c.company_id = %(company_id)s and c.type = %(content_type)s
      and (%(sub_types)s::text[] is null or c.sub_type = any(%(sub_types)s))
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
    update chunk_batch_requests
    set input_tokens = %s, output_tokens = %s, model = %s,
        cache_creation_input_tokens = %s, cache_read_input_tokens = %s
    where batch_id = %s and custom_id = %s
"""


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def get_companies() -> list[tuple[str, str, str]]:
    """Returns [(company_id, ticker, name), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select company_id, ticker, name from companies order by ticker")
            return cur.fetchall()


def get_pending(company_id: str, content_type: str, sub_types: list[str] | None = None) -> list[tuple]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(PENDING_SQL, {"company_id": company_id, "content_type": content_type, "sub_types": sub_types})
            return cur.fetchall()


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _build_request(custom_id: str, group: list[tuple], system_prompt: str, content_type: str) -> Request:
    lines = []
    for i, (chunk_id, content_id, start, end, storage_path) in enumerate(group):
        text = get_chunk_content(content_id, content_type, storage_path, start, end)
        lines.append(f"Item {i}:\n{text}")

    return Request(
        custom_id=custom_id,
        params={
            "model": MODEL,
            "max_tokens": 2048,
            "system": [
                {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            ],
            "messages": [{"role": "user", "content": "\n\n".join(lines)}],
            "tools": [_tool_schema(len(group))],
            "tool_choice": {"type": "tool", "name": "classify_second_pass"},
        },
    )


def _prewarm_cache(system_prompt: str) -> None:
    """Writes the cache entry via a normal synchronous call BEFORE the
    batch is submitted. Necessary because Batch API requests can be
    processed in parallel across different nodes -- there's no guarantee
    the "first" request in a batch actually finishes writing the cache
    before others check for a hit, so without this, many/most requests in
    a batch could simultaneously miss. max_tokens=0 is the documented
    pre-warm pattern for a synchronous call (it's explicitly rejected
    INSIDE a batch request, but fine here, since this call happens
    outside the batch)."""
    _client().messages.create(
        model=MODEL,
        max_tokens=0,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        messages=[{"role": "user", "content": "warmup"}],
    )


def submit_batch(limit_groups_per_pair: int | None = None) -> str:
    """Builds requests across every (company, content_type) pair with any
    pending chunks, and submits them all as ONE Anthropic Batch. Each
    pair's cache is pre-warmed with a synchronous call first -- see
    _prewarm_cache."""
    companies = get_companies()

    requests = []
    group_chunk_ids = []
    for content_label, spec in CONTENT_TYPES.items():
        content_type, sub_types, group_size = spec["type"], spec["sub_types"], spec["group_size"]
        for company_id, ticker, company_name in companies:
            pending = get_pending(company_id, content_type, sub_types)
            if not pending:
                continue

            system_prompt = _system_prompt(content_label, ticker, company_name)
            _prewarm_cache(system_prompt)
            print(f"  pre-warmed cache for {ticker}/{content_label}")

            groups = _chunk(pending, group_size)
            if limit_groups_per_pair is not None:
                groups = groups[:limit_groups_per_pair]

            for i, group in enumerate(groups):
                custom_id = f"{ticker}-{content_label}-{i}"
                requests.append(_build_request(custom_id, group, system_prompt, content_type))
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

    print(f"submitted batch {batch.id}: {len(requests)} requests, {len(all_chunk_ids)} chunks "
          f"across {len({cid for _, cids in group_chunk_ids for cid in cids})} chunks, "
          f"{len(companies)} companies x {len(CONTENT_TYPES)} content types")
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
                    getattr(usage, "cache_creation_input_tokens", None),
                    getattr(usage, "cache_read_input_tokens", None),
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
