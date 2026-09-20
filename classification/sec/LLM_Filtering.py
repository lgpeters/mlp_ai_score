"""Round-based, query-agnostic RAG classification for SEC filing chunks.

Unlike classification/news/LLM_Filtering.py (which classifies every HN
chunk directly -- cheap, since HN chunks are single-line titles), SEC
filings are long and dense (37k+ chunks across only ~140 documents, ~268
chunks/doc average) -- classifying every chunk isn't worth the cost. This
module instead runs a small set of embedding queries per document (reusing
the FinLang embeddings already computed for search), and only classifies
the chunks that rank as plausible AI-content candidates.

QUERY-AGNOSTIC MERGING: the queries in QUERIES exist purely to generate a
diverse candidate pool per document -- each contributes its own top
TOP_K_PER_QUERY chunks, but once merged, deduped by chunk_id (keeping each
chunk's best/lowest distance across whichever query(ies) surfaced it), and
sorted by distance, the algorithm no longer cares which query produced
which candidate. A document where every query converges on the same few
chunks gets a small candidate set; one where queries diverge gets a
larger one -- itself a signal about how concentrated a filing's AI content
is, not just noise to average away.

ROUND-BASED, NOT ONE-SHOT: Anthropic Batches are fire-and-forget (no way
to peek at intermediate results mid-batch), so continuing past round 1 is
a decision made BETWEEN rounds, not automated inside this module:
  - Round 1: every document in scope gets its merged top candidates
    classified (up to 5 queries x TOP_K_PER_QUERY each, deduped).
  - Every chunk_classifications row is stamped with the round number it
    was (most recently) submitted/scored in -- query chunk_classifications
    grouped by content_id/round yourself to see each round's hit rate
    (share of ai_associated > 0) and decide which documents are worth
    another round. There is deliberately no automated stopping rule here
    (an earlier version used a consecutive-miss-streak heuristic on
    ranked distance; it was replaced because it was fragile to retrieval
    noise and ignored sample size) -- you decide, informed by the data.
  - Round 2+: chunks already in chunk_classifications drop out of
    candidate generation automatically (anti-join), so only NOT-yet-seen
    candidates get pulled forward; scope the content_ids you pass in to
    whichever documents you've decided are worth continuing.
  - MAX_CHUNKS_PER_DOC is still a hard safety cap regardless of your
    decision, so a maximally AI-dense filing (e.g. NVDA's 10-K) can't
    silently consume the whole budget if you keep re-including it.

company_relevant is NOT scored here (unlike the news module) -- a SEC
filing chunk is inherently about its filing company by construction (it's
filed under that company's own CIK), so there's no "coincidental keyword
match" problem the way an HN title search has. It's hardcoded to true
whenever a score is written (see WRITE_SCORE_SQL), not asked of the
model -- the structured tool response only asks for ai_associated.

NOT YET RUN -- requires ANTHROPIC_API_KEY in .env. classification_prompt.md
in this directory is a placeholder copied verbatim from classification/
news/ -- rewrite it for SEC-filing-length prose chunks before running.
"""

import math
import os
from pathlib import Path

from anthropic import Anthropic
from anthropic.types.messages.batch_create_params import Request
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

from shared.db import get_connection
from shared.storage import get_source_text

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
GROUP_SIZE = 4

# Merged, query-agnostic candidate generation: run every query below
# against a document's not-yet-scored chunks, take each query's top
# TOP_K_PER_QUERY, then dedup by chunk_id (keeping the best/lowest
# distance) and sort. PLACEHOLDERS -- tune wording once real results come
# back; they exist purely to generate a diverse candidate pool, not to
# attribute a category (see classification_prompt.md for that judgment).
QUERIES = [
    "The Company has adopted artificial intelligence or machine learning technology to improve "
    "AI Machine Learning operational efficiency, productivity, or margins.",
    "Artificial intelligence presents risks to our business, including competitive "
    "Artificial intelligence (AI) cost pressure, or disruption to our end markets.",
    "entered into partnerships, acquisitions, mergers or infrastructure investment in artifical intelligence"
    "Artificial intelligence, GPUs, data centers, chip manufacturing",
    "Artificial Intelligence new products, conducting novel research and development. "
    "generative AI, large language models, or machine learning.",
    "artificial intelligence or machine learning relating torestructuring, hiring or headcount"
]
TOP_K_PER_QUERY = 5

# Hard safety cap regardless of how many rounds you choose to run for a
# document -- see module docstring. First set to 50, which turned out to
# be the actively binding constraint on nearly every high-signal document
# (NVDA/ADBE/TEAM/etc, each with 150-300+ chunks still unprocessed and
# still-strong hit rates at round 3), not natural exhaustion. Raised to
# 200, then to 5000 (effectively "don't bind at all" -- no SEC document
# has anywhere near 5000 chunks) to run an automated multi-round loop
# without the cap silently cutting anything off again.
MAX_CHUNKS_PER_DOC = 5000

PROMPT_PATH = Path(__file__).resolve().parent / "classification_prompt.md"

_TOOL_USE_INSTRUCTION = (
    "\n\nEach request contains multiple numbered items (Item 0, Item 1, ...). Use the "
    "classify_chunks tool to return one result per input item, in the same order, each "
    "tagged with its index."
)
SYSTEM_PROMPT = PROMPT_PATH.read_text() + _TOOL_USE_INSTRUCTION

_model: SentenceTransformer | None = None
_query_vecs: list[list[float]] | None = None


def _embed_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("FinLang/finance-embeddings-investopedia")
    return _model


def query_vectors() -> list[list[float]]:
    """Encodes QUERIES once and caches -- these are fixed for a whole run,
    no reason to re-embed them per document."""
    global _query_vecs
    if _query_vecs is None:
        _query_vecs = [v.tolist() for v in _embed_model().encode(QUERIES)]
    return _query_vecs


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    return 1.0 - dot / (norm_a * norm_b)


def _parse_pgvector_text(text: str) -> list[float]:
    """halfvec's ::text cast is "[0.1,0.2,...]" -- parsed by hand rather
    than via a cast to a Postgres array type, since pgvector's halfvec
    doesn't reliably support ::real[] casting across versions."""
    return [float(x) for x in text.strip("[]").split(",")]


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _tool_schema(n: int) -> dict:
    """Only the AI-relevance gate -- company_relevant is hardcoded true
    when writing scores (see WRITE_SCORE_SQL), not asked of the model,
    since a SEC filing chunk is inherently about its filing company by
    construction."""
    return {
        "name": "classify_chunks",
        "description": "Return AI-association gates for each input chunk.",
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
                            "ai_associated": {"type": "integer", "enum": [0, 1, 2, 3]},
                        },
                        "required": ["index", "ai_associated"],
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


# ---------------------------------------------------------------------------
# Scoping helper -- e.g. get_10k_filings(2023) for the "10-Ks in 2023" sense
# check. Returns content_ids; every other function in this module takes a
# content_ids scope so it composes with whatever scope the caller wants.
# ---------------------------------------------------------------------------

GET_10K_FILINGS_SQL = """
    select content_id
    from content
    where type = 'sec_filing' and sub_type = '10-K' and extract(year from publication_date) = %s
"""


def get_10k_filings(year: int) -> list[str]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GET_10K_FILINGS_SQL, (year,))
            return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

UNPROCESSED_CHUNKS_SQL = """
    select ci.chunk_id, ci.content_id, ci.start_offset, ci.end_offset, ci.finlang_embedding::text
    from content_index ci
    join content c using (content_id)
    left join chunk_classifications cc using (chunk_id)
    where c.content_id = any(%(content_ids)s)
      and ci.finlang_embedding is not null
      and (cc.chunk_id is null or cc.ai_associated is null)
"""

def _capped_content_ids(content_ids: list[str]) -> set[str]:
    """Documents that have already reached MAX_CHUNKS_PER_DOC scored chunks."""
    if not content_ids:
        return set()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select ci.content_id, count(*)
                from content_index ci
                join chunk_classifications cc using (chunk_id)
                where ci.content_id = any(%(content_ids)s) and cc.ai_associated is not null
                group by ci.content_id
                having count(*) >= %(cap)s
                """,
                {"content_ids": content_ids, "cap": MAX_CHUNKS_PER_DOC},
            )
            return {row[0] for row in cur.fetchall()}


def active_content_ids(content_ids: list[str]) -> list[str]:
    """content_ids minus docs at the MAX_CHUNKS_PER_DOC cap. No automated
    dead-end detection -- that decision is yours; see module docstring."""
    excluded = _capped_content_ids(content_ids)
    return [cid for cid in content_ids if cid not in excluded]


def _next_round_number(content_ids: list[str]) -> int:
    """1 + the highest round already recorded for any chunk belonging to
    these content_ids (0 if none yet). Derived fresh from
    chunk_classifications every call, not tracked by the caller."""
    if not content_ids:
        return 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(max(cc.round), 0)
                from chunk_classifications cc
                join content_index ci using (chunk_id)
                where ci.content_id = any(%s)
                """,
                (content_ids,),
            )
            return cur.fetchone()[0] + 1


def get_round_candidates(content_ids: list[str]) -> list[tuple[str, str, int, int]]:
    """Query-agnostic merged candidate generation for one round. Returns
    [(chunk_id, content_id, start_offset, end_offset), ...] sorted by
    distance (best first), restricted to documents still active and
    chunks not yet in chunk_classifications."""
    active = active_content_ids(content_ids)
    if not active:
        return []

    qvecs = query_vectors()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(UNPROCESSED_CHUNKS_SQL, {"content_ids": active})
            rows = cur.fetchall()

    by_doc: dict[str, list[tuple[str, int, int, list[float]]]] = {}
    for chunk_id, content_id, start, end, emb_text in rows:
        by_doc.setdefault(content_id, []).append((chunk_id, start, end, _parse_pgvector_text(emb_text)))

    candidates: list[tuple[float, str, str, int, int]] = []
    for content_id, chunks in by_doc.items():
        best_dist: dict[str, tuple[float, int, int]] = {}
        for qvec in qvecs:
            ranked = sorted(
                ((chunk_id, start, end, _cosine_distance(vec, qvec)) for chunk_id, start, end, vec in chunks),
                key=lambda x: x[3],
            )[:TOP_K_PER_QUERY]
            for chunk_id, start, end, dist in ranked:
                if chunk_id not in best_dist or dist < best_dist[chunk_id][0]:
                    best_dist[chunk_id] = (dist, start, end)

        for chunk_id, (dist, start, end) in best_dist.items():
            candidates.append((dist, chunk_id, content_id, start, end))

    candidates.sort(key=lambda x: x[0])
    return [(chunk_id, content_id, start, end) for _, chunk_id, content_id, start, end in candidates]


# ---------------------------------------------------------------------------
# Batch submission / result fetching -- same two-phase pattern as
# classification/news/LLM_Filtering.py, reusing chunk_batch_requests.
# ---------------------------------------------------------------------------

MARK_SUBMITTED_SQL = """
    insert into chunk_classifications (chunk_id, batch_id, round)
    select x, %s, %s from unnest(%s::text[]) as x
    on conflict (chunk_id) do update set batch_id = excluded.batch_id, round = excluded.round
"""

TRACK_REQUEST_SQL = """
    insert into chunk_batch_requests (batch_id, custom_id, chunk_ids, round)
    values (%s, %s, %s, %s)
"""

# company_relevant hardcoded true -- see module docstring.
WRITE_SCORE_SQL = """
    insert into chunk_classifications (chunk_id, company_relevant, ai_associated, scored_at, batch_id, round)
    values (%s, true, %s, now(), null, %s)
    on conflict (chunk_id) do update set
        company_relevant = true,
        ai_associated = excluded.ai_associated,
        scored_at = excluded.scored_at,
        batch_id = null,
        round = excluded.round
"""

CLEAR_BATCH_SQL = "update chunk_classifications set batch_id = null where chunk_id = any(%s)"

RECORD_USAGE_SQL = """
    update chunk_batch_requests set input_tokens = %s, output_tokens = %s, model = %s
    where batch_id = %s and custom_id = %s
"""

COMPANY_INFO_SQL = """
    select c.content_id, co.name, c.storage_path
    from content c join companies co using (company_id)
    where c.content_id = any(%(content_ids)s)
"""


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _build_request(
    custom_id: str,
    group: list[tuple],
    company_names: dict[str, str],
    storage_paths: dict[str, str],
    text_cache: dict[str, str],
) -> Request:
    # Matches classification/news's format: "Item N:" header, then company
    # name, then the chunk text (a paragraph here, not a single title).
    lines = []
    for i, (chunk_id, content_id, start, end) in enumerate(group):
        if content_id not in text_cache:
            text_cache[content_id] = get_source_text(content_id, "sec_filing", storage_paths[content_id])
        text = text_cache[content_id][start:end]
        lines.append(f"Item {i}:\n{company_names[content_id]}\n{text}")

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


def submit_round(content_ids: list[str], limit_groups: int | None = None) -> str:
    """Generates this round's merged candidates across content_ids and
    submits them as one Anthropic Batch. Returns "" if nothing pending."""
    candidates = get_round_candidates(content_ids)
    if limit_groups is not None:
        candidates = candidates[: limit_groups * GROUP_SIZE]

    if not candidates:
        print("nothing pending -- no active documents with remaining candidates")
        return ""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(COMPANY_INFO_SQL, {"content_ids": list({cid for _, cid, _, _ in candidates})})
            info = cur.fetchall()
    company_names = {content_id: name for content_id, name, _ in info}
    storage_paths = {content_id: path for content_id, _, path in info}

    groups = _chunk(candidates, GROUP_SIZE)
    text_cache: dict[str, str] = {}
    requests = []
    group_chunk_ids = []
    for i, group in enumerate(groups):
        custom_id = f"group-{i}"
        requests.append(_build_request(custom_id, group, company_names, storage_paths, text_cache))
        group_chunk_ids.append((custom_id, [row[0] for row in group]))

    round_number = _next_round_number(content_ids)
    batch = _client().messages.batches.create(requests=requests)
    all_chunk_ids = [chunk_id for _, chunk_ids in group_chunk_ids for chunk_id in chunk_ids]

    with get_connection() as conn:
        with conn.cursor() as cur:
            for custom_id, chunk_ids in group_chunk_ids:
                cur.execute(TRACK_REQUEST_SQL, (batch.id, custom_id, chunk_ids, round_number))
            cur.execute(MARK_SUBMITTED_SQL, (batch.id, round_number, all_chunk_ids))

    print(f"submitted batch {batch.id} (round {round_number}): {len(requests)} requests, "
          f"{len(candidates)} chunks across {len({cid for _, cid, _, _ in candidates})} documents")
    return batch.id


def fetch_round(batch_id: str) -> None:
    """Call once the batch has finished. Safe to call before that -- just
    reports it's still processing."""
    client = _client()
    batch = client.messages.batches.retrieve(batch_id)

    if batch.processing_status != "ended":
        print(f"batch {batch_id} still processing (status: {batch.processing_status})")
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select custom_id, chunk_ids from chunk_batch_requests where batch_id = %s", (batch_id,)
            )
            rows = cur.fetchall()
    chunk_ids_by_custom_id = {custom_id: chunk_ids for custom_id, chunk_ids in rows}
    all_chunk_ids = [cid for chunk_ids in chunk_ids_by_custom_id.values() for cid in chunk_ids]

    # Round is computed per DOCUMENT (content_id), not per batch -- a
    # single batch submission can mix documents at very different depths
    # (e.g. a fresh document alongside one already on its 5th round), so
    # one shared round number for the whole batch mislabels most of it.
    # Look up each chunk's document and use 1 + however many distinct
    # rounds that SPECIFIC document has already been scored in, computed
    # fresh right before writing (not trusted from chunk_batch_requests,
    # which only knows the batch-level submission, not the per-document
    # truth).
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select chunk_id, content_id from content_index where chunk_id = any(%s)",
                (all_chunk_ids,),
            )
            content_id_by_chunk = dict(cur.fetchall())

            cur.execute(
                """
                select ci.content_id, coalesce(max(cc.round), 0)
                from content_index ci
                left join chunk_classifications cc
                    on cc.chunk_id = ci.chunk_id and cc.ai_associated is not null
                where ci.content_id = any(%s)
                group by ci.content_id
                """,
                (list(set(content_id_by_chunk.values())),),
            )
            next_round_by_content_id = {cid: max_round + 1 for cid, max_round in cur.fetchall()}

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
                    chunk_id = chunk_ids[item["index"]]
                    round_number = next_round_by_content_id[content_id_by_chunk[chunk_id]]
                    cur.execute(WRITE_SCORE_SQL, (chunk_id, item["ai_associated"], round_number))
                    scored += 1

    print(f"batch {batch_id}: {scored} scored, {failed} failed (cleared for retry)")


def run_loop(content_ids: list[str], max_rounds: int = 20) -> None:
    """Convenience driver: submit a round, wait for it, fetch it, repeat
    until no active document has remaining candidates or max_rounds is
    hit. NOT a background/async-safe loop -- blocks on each batch via
    polling. Intended for manual/sense-check runs, not production use
    (which should call submit_round/fetch_round separately, since batches
    can take hours)."""
    import time

    for round_num in range(max_rounds):
        batch_id = submit_round(content_ids)
        if not batch_id:
            print(f"done after {round_num} rounds")
            return

        while True:
            batch = _client().messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                break
            time.sleep(30)

        fetch_round(batch_id)

    print(f"stopped after hitting max_rounds={max_rounds}")


def run_loop_with_threshold(content_ids: list[str], min_hits: int = 2, max_rounds: int = 3) -> None:
    """Like run_loop, but narrows content_ids BETWEEN rounds: after each
    round, keeps only documents that scored >= min_hits (ai_associated > 0)
    in that round, dropping the rest before submitting the next one.
    Formalizes the manual ">=2 hits to continue" rule used for rounds 1-3
    into something that can run rounds 4-6 unattended. Blocks on each
    batch via polling -- intended for a bounded run, not indefinite use."""
    import time

    active = list(content_ids)
    for _ in range(max_rounds):
        if not active:
            print("no documents left to continue")
            return

        round_number = _next_round_number(active)
        batch_id = submit_round(active)
        if not batch_id:
            print(f"done -- nothing pending for round {round_number}")
            return

        while True:
            batch = _client().messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                break
            time.sleep(30)

        fetch_round(batch_id)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select c.content_id
                    from chunk_classifications cc
                    join content_index ci using (chunk_id)
                    join content c using (content_id)
                    where cc.round = %s and c.content_id = any(%s)
                    group by c.content_id
                    having sum((cc.ai_associated > 0)::int) >= %s
                    """,
                    (round_number, active, min_hits),
                )
                active = [row[0] for row in cur.fetchall()]
        print(f"round {round_number} done -- {len(active)} documents continuing")

    print(f"stopped after {max_rounds} rounds, {len(active)} documents still active")


if __name__ == "__main__":
    pass
