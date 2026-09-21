"""Generates a static HTML review report summarizing pipeline coverage,
first-pass (dynamic RAG) retrieval results, and second-pass classification
results, for sending to an external reviewer.

Pulls everything live from the DB (no cached/precomputed numbers) --
document/chunk counts, per-type dynamic-retrieval hit rates, and per-company
materiality/saliency extremes from chunk_classifications. Pipeline
descriptions (source/coverage/considerations/known issues) are hand-written
summaries of the real docs in pipelines/*/DataIngestion.md,
pipelines/*/COVERAGE_EXCEPTIONS.md, and pipelines/sec/README.md -- kept here
as static text rather than parsed live, since those docs are prose, not
structured data.

Usage:
    python3 generate_review_report.py [output_path]

Default output: review_report.html (repo root).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.db import get_connection
from shared.storage import get_chunk_content

REPO_ROOT = Path(__file__).resolve().parent

STAGE_1_MODEL = "Haiku 4.5"
STAGE_2_MODEL = "Sonnet 5"
STAGE_2_TITLE = f"2 - Focused Materiality and Salience Ranking ({STAGE_2_MODEL})"
STAGE_2_NOTE = "Prompt-Caching + Batching"

CONTENT_TYPES = [
    {
        "key": "sec_filing",
        "label": "SEC Filings",
        "stage_1_title": f"1 - Dynamic RAG AI Classification ({STAGE_1_MODEL})",
        "stage_1_pruned": True,
        "description": {
            "source": "SEC EDGAR (company + filing metadata via the submissions API; "
                       "full text downloaded and converted to markdown via edgartools).",
            "coverage": "9 companies, 10 CIKs (9 current + 1 historical alias for Dell). "
                        "~2,194 filings after 8-K narrowing: ~1,681 8-K, ~362 10-Q, ~123 10-K, "
                        "~21 S-4/S-4-A, 7 20-F. Date window 2012-present (a safety buffer ahead "
                        "of the real 2015-present analysis window, since filing dates can lag "
                        "the fiscal period they report on). 100% of discovered filings have full "
                        "markdown downloaded -- 0 outstanding download failures.",
            "considerations": "Only 10-K/10-K-A, 10-Q/10-Q-A, 20-F/20-F-A, S-4/S-4-A, and "
                               "8-K/8-K-A are ingested -- the form types most likely to carry "
                               "substantive business narrative. 8-K (by far the highest-volume "
                               "form) is narrowed to filings tagged with at least one of 6 item "
                               "codes (material agreements, completed acquisitions, earnings, "
                               "exec appointments, Reg FD disclosures, other events) -- a "
                               "judgment call to balance signal against noise/cost, not "
                               "exhaustive. S-4 is kept despite low volume because a target "
                               "company's business description in an acquisition filing is a "
                               "high-confidence signal when the deal is AI-related.",
            "known_issues": "Dell has a real, unfixable gap Nov 2013-Nov 2015: the company was "
                             "fully private (LBO) with no SEC reporting obligation and no public "
                             "securities during that window -- no filings exist anywhere for it "
                             "under any entity. Atlassian has no filings before its Dec 2015 IPO "
                             "(expected boundary, not a gap), and filed 20-F (foreign private "
                             "issuer) instead of 10-K/10-Q/8-K until its Sept 2022 Delaware "
                             "redomiciliation. The 6-item 8-K filter is a judgment call: a filing "
                             "with genuine AI content tagged under an excluded item code would be "
                             "silently skipped.",
        },
    },
    {
        "key": "transcripts",
        "label": "Earnings Call Transcripts",
        "stage_1_title": f"1 - Dynamic RAG AI Classification ({STAGE_1_MODEL})",
        "stage_1_pruned": True,
        "description": {
            "source": "stockanalysis.com (sourced upstream from Quartr, a third-party vendor) "
                       "-- chosen after evaluating and rejecting FMP (paywalled), API Ninjas "
                       "(Premium-only), and earningscalls.dev (250-char previews only, 2020+ "
                       "archive only).",
            "coverage": "896 total events discovered across all 9 companies (earnings calls "
                        "plus AGMs, investor days, conferences, fireside chats), tagged "
                        "is_earnings_call via an exact \"Q# YYYY\" title match. 100% download "
                        "success (872 on first pass, remaining 24 recovered via a legacy-format "
                        "fallback). Earnings-call-specific counts per company range ~61-66 for "
                        "the long-public names down to 43 for Atlassian and 39-41 for Dell.",
            "considerations": "Every event type is ingested at the metadata level (not just "
                               "earnings calls) to preserve optionality, since investor days and "
                               "fireside chats may carry genuine AI-relevant content too. Scraped "
                               "via an embedded, undocumented JS object on the transcript-list "
                               "page (not a stable public API) -- inherently fragile to upstream "
                               "markup changes, accepted given no viable free/cheap official API "
                               "existed. Second-pass (materiality/saliency) classification is "
                               "deliberately scoped to earnings_call only, an explicit choice to "
                               "exclude the other event types from that stage.",
            "known_issues": "Dell: transcript coverage on this source starts 2022 -- a larger "
                             "gap than the SEC-side private-company gap, since Dell traded under "
                             "ticker DVMT (not DELL) from 2016-2018 and stockanalysis.com has no "
                             "page for that ticker at all; coverage for the dell slug itself "
                             "doesn't start until 2022 even though the company was NYSE: DELL "
                             "again by 2019, an unexplained vendor gap. Caterpillar: coverage "
                             "starts 2019-04-24 despite being public long before -- confirmed a "
                             "genuine limitation of Quartr's own coverage, not a scraping bug on "
                             "our end. Atlassian: earliest transcript is 2016, consistent with "
                             "its Dec 2015 IPO (expected boundary, not a gap).",
        },
    },
    {
        "key": "hackernews",
        "label": "Hacker News",
        "stage_1_title": f"1 - Simple AI Classification ({STAGE_1_MODEL})",
        "stage_1_pruned": False,
        "description": {
            "source": "Hacker News Algolia Search API (free, no auth). Content model is story "
                      "metadata only (title, url, points, author, comment count) -- not the "
                      "linked article's body, since HN doesn't host that and following "
                      "arbitrary external links carries scraping/ToS risk.",
            "coverage": "5,684 stories across all 9 companies, 2015-01 through 2026-09 (141 "
                        "months), queried per company per month using both the formal legal "
                        "name and colloquial aliases. Per company: ADBE 1,059, INTC 1,364, "
                        "NVDA 1,283, DELL 736, TEAM 584, MU 302, CAT 85, WM 28, PG 14.",
            "considerations": "Aliases were necessary -- the formal legal name alone returned "
                               "zero real hits for genuinely newsworthy periods (e.g. \"Nvidia "
                               "Corporation\" vs. how people actually write \"Nvidia\"). Matching "
                               "is whole-word, not substring (\"micron\" must not match inside "
                               "\"micronation\"), and results are filtered before ranking by "
                               "points, not after -- an earlier rank-then-filter ordering let "
                               "high-point noise (Microsoft stories crowding out \"Micron\" via "
                               "substring overlap) silently discard genuine lower-point stories "
                               "before they were ever stored. Genuine reposts (same title, "
                               "different story_id) are deduplicated to their single "
                               "highest-points occurrence.",
            "known_issues": "Caterpillar (85) and Waste Management (28) remain genuinely "
                             "low-volume even after every fix -- confirmed as a real "
                             "characteristic of a developer-skewed forum's interests (industrial/"
                             "consumer-staples companies aren't much discussed here), not a bug, "
                             "surviving multiple rounds of query/alias fixes without materially "
                             "changing. The whole-word filter only catches \"doesn't name the "
                             "company at all\" -- it says nothing about whether AI content in a "
                             "surviving story is genuine or material, which is deferred entirely "
                             "to the classification stages below. Comment-thread text is not yet "
                             "ingested (a legitimate future source, held back deliberately).",
        },
    },
]

# Dynamic-retrieval stats (the "considered / AI relevant" numbers, both the
# headline sentence and the by-ticker table) are scoped to the sub_types
# that carry real narrative content for that source -- otherwise the
# percentages get diluted/skewed by sub_types that don't belong in this
# comparison: SEC's 8-K/S-4 are event-driven filings with very different
# relevance dynamics from the core periodic disclosures, and transcripts'
# "other" events (AGMs, investor days, conferences) were never in scope for
# the earnings-call-only second pass, so mixing them in would misrepresent
# the ranking below. None means no restriction (Hacker News has one sub_type).
SUB_TYPE_FILTERS = {
    "sec_filing": ["10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "6-K", "6-K/A"],
    "transcripts": ["earnings_call"],
    "hackernews": None,
}

DOC_COUNT_SQL = "select type, count(distinct content_id) from content group by type"

CHUNK_COUNT_SQL = """
    select c.type, count(*)
    from content_index ci
    join content c using (content_id)
    where ci.finlang_embedding is not null
    group by c.type
"""

DYNAMIC_RETRIEVAL_SQL = """
    select
        count(*) as considered,
        count(*) filter (where cc.ai_associated > 0) as ai_relevant
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    where cc.ai_associated is not null
      and c.type = %(content_type)s
      and (%(sub_types)s::text[] is null or c.sub_type = any(%(sub_types)s))
"""

TICKER_RETRIEVAL_SQL = """
    select co.ticker,
           count(*) as considered,
           count(*) filter (where cc.ai_associated > 0) as ai_relevant
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where cc.ai_associated is not null
      and c.type = %(content_type)s
      and (%(sub_types)s::text[] is null or c.sub_type = any(%(sub_types)s))
    group by co.ticker
    order by co.ticker
"""

RANKED_5_SQL = """
    select ci.content_id, ci.start_offset, ci.end_offset, c.storage_path, c.type,
           c.sub_type, c.publication_date, cc.ai_category, cc.ai_materiality_score,
           cc.ai_saliency_score, co.ticker
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where c.type = %(content_type)s
      and cc.{field} is not null
    order by cc.{field} {direction}
    limit 5
"""

AI_CATEGORY_LABELS = {
    1: "AI Partnership", 2: "Operational Efficiencies", 3: "Restructuring",
    4: "Clear Positive", 5: "Clear Negative", 6: "Product Roadmap",
}


def fetch_counts() -> tuple[dict, dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(DOC_COUNT_SQL)
            doc_counts = dict(cur.fetchall())

            cur.execute(CHUNK_COUNT_SQL)
            chunk_counts = dict(cur.fetchall())

    return doc_counts, chunk_counts


def fetch_retrieval(content_type: str) -> dict:
    params = {"content_type": content_type, "sub_types": SUB_TYPE_FILTERS[content_type]}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(DYNAMIC_RETRIEVAL_SQL, params)
            considered, ai_relevant = cur.fetchone()

            cur.execute(TICKER_RETRIEVAL_SQL, params)
            by_ticker = [
                {"ticker": ticker, "considered": c, "ai_relevant": r}
                for ticker, c, r in cur.fetchall()
            ]

    return {"considered": considered or 0, "ai_relevant": ai_relevant or 0, "by_ticker": by_ticker}


def serialize_row(row: tuple, snippet_chars: int = 280) -> dict:
    (content_id, start, end, storage_path, content_type, sub_type, pub_date,
     category, materiality, saliency, ticker) = row
    full_text = get_chunk_content(content_id, content_type, storage_path, start, end).strip()
    flat = full_text.replace("\n", " ")
    snippet = flat if len(flat) <= snippet_chars else flat[:snippet_chars].rsplit(" ", 1)[0] + "..."
    return {
        "ticker": ticker,
        "sub_type": sub_type,
        "publication_date": pub_date.isoformat() if pub_date else None,
        "ai_category": category,
        "ai_category_label": AI_CATEGORY_LABELS.get(category),
        "ai_materiality_score": materiality,
        "ai_saliency_score": saliency,
        "snippet": snippet,
        "full_text": full_text,
    }


def fetch_ranked_5(content_type: str, field: str, direction: str) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(RANKED_5_SQL.format(field=field, direction=direction), {"content_type": content_type})
            rows = cur.fetchall()
    return [serialize_row(r) for r in rows]


def build_report() -> dict:
    doc_counts, chunk_counts = fetch_counts()

    report_types = []
    for ct in CONTENT_TYPES:
        key = ct["key"]
        retrieval = fetch_retrieval(key)

        by_ticker = [
            {
                "ticker": row["ticker"],
                "considered": row["considered"],
                "ai_relevant": row["ai_relevant"],
                "pct": round(row["ai_relevant"] / row["considered"] * 100, 1) if row["considered"] else 0.0,
            }
            for row in retrieval["by_ticker"]
        ]

        report_types.append({
            "key": key,
            "label": ct["label"],
            "stage_1_title": ct["stage_1_title"],
            "stage_1_pruned": ct["stage_1_pruned"],
            "sub_type_scope": SUB_TYPE_FILTERS[key],
            "description": ct["description"],
            "doc_count": doc_counts.get(key, 0),
            "chunk_count": chunk_counts.get(key, 0),
            "considered": retrieval["considered"],
            "ai_relevant": retrieval["ai_relevant"],
            "by_ticker": by_ticker,
            "materiality_top": fetch_ranked_5(key, "ai_materiality_score", "desc"),
            "materiality_bottom": fetch_ranked_5(key, "ai_materiality_score", "asc"),
            "saliency_top": fetch_ranked_5(key, "ai_saliency_score", "desc"),
            "saliency_bottom": fetch_ranked_5(key, "ai_saliency_score", "asc"),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage_2_title": STAGE_2_TITLE,
        "stage_2_note": STAGE_2_NOTE,
        "content_types": report_types,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Engagement Pipeline &mdash; Review Report</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<style>
  :root {{
    --bg: #f7f7f5; --panel: #ffffff; --border: #e2e0da; --text: #24231f;
    --muted: #6b6a64; --accent: #a8560f; --accent-soft: #f4e6d8;
    --pos: #2e6b3e; --pos-bg: #e8f3ea; --neg: #a1352b; --neg-bg: #fbe9e7;
    --code-bg: #f1efe9;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2.5rem 1.5rem 4rem; background: var(--bg); color: var(--text);
    font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
    line-height: 1.55;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  h1 {{
    font-size: 1.9rem; margin: 0 0 0.25rem; letter-spacing: -0.01em;
    font-family: "Iowan Old Style", Georgia, serif;
  }}
  .subtitle {{ color: var(--muted); font-size: 0.95rem; margin-bottom: 2rem; }}
  .subtitle code {{ font-family: "SF Mono", Menlo, monospace; font-size: 0.85em; }}

  .tabbar {{ display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 1.5rem; border-bottom: 2px solid var(--border); }}
  .tabbar button {{
    font-family: inherit; font-size: 0.95rem; padding: 0.6rem 1.1rem; border: none;
    background: none; color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent;
    margin-bottom: -2px; transition: color 0.15s;
  }}
  .tabbar button:hover {{ color: var(--text); }}
  .tabbar button.active {{ color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }}

  .panel {{ display: none; }}
  .panel.active {{ display: block; }}

  section {{ margin-bottom: 2.2rem; }}
  h2 {{
    font-size: 1.1rem; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--accent); margin: 0 0 0.9rem; font-family: -apple-system, sans-serif;
  }}

  .stat-row {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem; }}
  .stat-card {{
    flex: 1; min-width: 160px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 0.9rem 1.1rem;
  }}
  .stat-card .num {{
    font-family: "SF Mono", Menlo, monospace; font-size: 1.6rem; font-weight: 600;
    font-variant-numeric: tabular-nums; color: var(--text);
  }}
  .stat-card .label {{ font-size: 0.78rem; color: var(--muted); font-family: -apple-system, sans-serif; margin-top: 0.15rem; }}

  details.desc {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 0.75rem 1.1rem; margin-top: 0.5rem;
  }}
  details.desc summary {{
    cursor: pointer; font-family: -apple-system, sans-serif; font-size: 0.88rem;
    font-weight: 600; color: var(--muted);
  }}
  details.desc summary:hover {{ color: var(--text); }}
  .desc-body {{ margin-top: 0.8rem; font-size: 0.95rem; }}
  .desc-body h4 {{
    font-family: -apple-system, sans-serif; font-size: 0.75rem; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--accent); margin: 0.9rem 0 0.3rem;
  }}
  .desc-body h4:first-child {{ margin-top: 0; }}

  .retrieval-sentence {{
    background: var(--accent-soft); border-radius: 8px; padding: 0.9rem 1.1rem;
    font-size: 1rem;
  }}
  .retrieval-sentence b {{ font-family: "SF Mono", Menlo, monospace; }}
  .pct-line {{
    margin-top: 0.5rem; font-size: 0.82rem; font-family: -apple-system, sans-serif;
    color: var(--muted);
  }}
  .flag-note {{
    font-family: -apple-system, sans-serif; font-size: 0.78rem; color: var(--muted);
    background: var(--code-bg); border-left: 3px solid var(--accent); border-radius: 4px;
    padding: 0.5rem 0.75rem; margin-bottom: 0.6rem;
  }}
  .flag-note a {{ color: var(--accent); font-weight: 600; text-decoration: none; }}
  .flag-note a:hover {{ text-decoration: underline; }}

  .ticker-table-wrap {{ margin-top: 1rem; }}
  .ticker-table-title {{
    font-family: -apple-system, sans-serif; font-size: 0.8rem; color: var(--muted);
    margin: 0 0 0.5rem; font-weight: 600;
  }}
  table.ticker-table {{
    width: 100%; max-width: 480px; border-collapse: collapse; background: var(--panel);
    border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
    font-family: -apple-system, sans-serif; font-size: 0.85rem;
  }}
  table.ticker-table th {{
    text-align: left; padding: 0.45rem 0.7rem; background: var(--code-bg);
    color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em;
    font-weight: 600;
  }}
  table.ticker-table td {{ padding: 0.4rem 0.7rem; border-top: 1px solid var(--border); }}
  table.ticker-table .ticker-cell {{ font-weight: 700; }}
  table.ticker-table .num-cell {{ font-family: "SF Mono", Menlo, monospace; font-variant-numeric: tabular-nums; }}
  .pct-cell {{ min-width: 130px; }}
  .pct-bar-wrap {{ position: relative; height: 1.1rem; background: var(--code-bg); border-radius: 4px; overflow: hidden; }}
  .pct-bar {{ position: absolute; inset: 0; background: var(--accent-soft); border-right: 2px solid var(--accent); }}
  .pct-bar-wrap span {{
    position: relative; z-index: 1; display: block; text-align: right; padding: 0 0.4rem;
    line-height: 1.1rem; font-size: 0.78rem; font-weight: 600; color: var(--text);
  }}

  .stage-2-note {{
    font-family: -apple-system, sans-serif; font-size: 0.7rem; text-transform: none;
    letter-spacing: 0; color: var(--muted); font-weight: 400;
  }}

  .metric-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }}
  @media (max-width: 640px) {{ .metric-grid {{ grid-template-columns: 1fr; }} }}
  .metric-block h3 {{
    font-family: -apple-system, sans-serif; font-size: 0.85rem; margin: 0 0 0.5rem; color: var(--text);
  }}
  .rank-list {{ list-style: none; margin: 0 0 1rem; padding: 0; }}
  .rank-item {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
    padding: 0.55rem 0.75rem; margin-bottom: 0.4rem; font-size: 0.85rem;
    cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s;
  }}
  .rank-item:hover {{ border-color: var(--accent); box-shadow: 0 1px 4px rgba(0,0,0,0.06); }}
  .rank-item .snippet-hint {{
    margin-top: 0.35rem; font-family: -apple-system, sans-serif; font-size: 0.7rem;
    color: var(--accent);
  }}
  .rank-item .meta {{
    display: flex; justify-content: space-between; gap: 0.5rem; font-family: -apple-system, sans-serif;
    font-size: 0.72rem; color: var(--muted); margin-bottom: 0.3rem;
  }}
  .score-badge {{
    font-family: "SF Mono", Menlo, monospace; font-weight: 700; padding: 0.05rem 0.4rem;
    border-radius: 4px; white-space: nowrap;
  }}
  .score-badge.mat {{ background: var(--code-bg); color: var(--text); }}
  .score-badge.pos {{ background: var(--pos-bg); color: var(--pos); }}
  .score-badge.neg {{ background: var(--neg-bg); color: var(--neg); }}
  .snippet {{ color: var(--text); }}
  .empty-note {{ color: var(--muted); font-size: 0.85rem; font-style: italic; font-family: -apple-system, sans-serif; }}

  .modal-overlay {{
    position: fixed; inset: 0; background: rgba(30, 28, 22, 0.5);
    display: flex; align-items: flex-start; justify-content: center;
    padding: 4vh 1.5rem; z-index: 100; overflow-y: auto;
  }}
  .modal-overlay[hidden] {{ display: none; }}
  .modal-box {{
    background: var(--panel); border-radius: 10px; max-width: 720px; width: 100%;
    padding: 1.5rem 1.75rem 1.75rem; box-shadow: 0 12px 40px rgba(0,0,0,0.25);
  }}
  .modal-meta {{
    display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;
    font-family: -apple-system, sans-serif; font-size: 0.78rem; color: var(--muted);
    border-bottom: 1px solid var(--border); padding-bottom: 0.8rem; margin-bottom: 1rem;
  }}
  .modal-close {{
    font-family: -apple-system, sans-serif; font-size: 0.8rem; background: var(--code-bg);
    border: 1px solid var(--border); border-radius: 6px; padding: 0.25rem 0.6rem;
    cursor: pointer; color: var(--text); flex-shrink: 0;
  }}
  .modal-close:hover {{ background: var(--accent-soft); }}
  .modal-body {{ font-size: 0.98rem; }}
  .modal-body :first-child {{ margin-top: 0; }}
  .modal-body h1, .modal-body h2, .modal-body h3 {{ font-size: 1.05rem; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI Engagement Pipeline &mdash; Review Report</h1>
  <div class="subtitle">Generated <code id="generated-at"></code>. Live snapshot of ingestion coverage, dynamic-retrieval (first-pass) hit rates, and second-pass classification results.</div>

  <div class="tabbar" id="type-tabbar"></div>
  <div id="type-panels"></div>
</div>

<div class="modal-overlay" id="chunk-modal" hidden>
  <div class="modal-box">
    <div class="modal-meta">
      <div id="modal-meta-text"></div>
      <button class="modal-close" id="modal-close">Close</button>
    </div>
    <div class="modal-body" id="modal-body"></div>
  </div>
</div>

<script id="report-data" type="application/json">{report_json}</script>
<script>
const report = JSON.parse(document.getElementById("report-data").textContent);
document.getElementById("generated-at").textContent = new Date(report.generated_at).toLocaleString();

function fmt(n) {{ return n.toLocaleString(); }}
function pct(part, whole) {{ return whole > 0 ? (part / whole * 100).toFixed(1) : "0.0"; }}

function scoreBadges(item) {{
  let out = "";
  if (item.ai_materiality_score != null) {{
    out += `<span class="score-badge mat">M ${{item.ai_materiality_score}}/10</span> `;
  }}
  if (item.ai_saliency_score != null) {{
    const cls = item.ai_saliency_score > 0 ? "pos" : item.ai_saliency_score < 0 ? "neg" : "mat";
    out += `<span class="score-badge ${{cls}}">S ${{item.ai_saliency_score > 0 ? "+" : ""}}${{item.ai_saliency_score}}</span>`;
  }}
  return out;
}}

function renderTickerTable(byTicker) {{
  if (!byTicker || byTicker.length === 0) {{
    return `<div class="empty-note">No data yet.</div>`;
  }}
  const rows = byTicker.map(row => `
    <tr>
      <td class="ticker-cell">${{row.ticker}}</td>
      <td class="num-cell">${{fmt(row.considered)}}</td>
      <td class="num-cell">${{fmt(row.ai_relevant)}}</td>
      <td class="num-cell pct-cell">
        <div class="pct-bar-wrap">
          <div class="pct-bar" style="width:${{Math.min(row.pct, 100)}}%"></div>
          <span>${{row.pct.toFixed(1)}}%</span>
        </div>
      </td>
    </tr>
  `).join("");
  return `
    <table class="ticker-table">
      <thead><tr><th>Ticker</th><th>Considered</th><th>AI Relevant</th><th>% AI Relevant</th></tr></thead>
      <tbody>${{rows}}</tbody>
    </table>
  `;
}}

const chunkRegistry = [];

function renderRankList(items) {{
  if (!items || items.length === 0) {{
    return `<div class="empty-note">Not yet scored by the second pass.</div>`;
  }}
  return `<ul class="rank-list">` + items.map(item => {{
    const idx = chunkRegistry.length;
    chunkRegistry.push(item);
    return `
    <li class="rank-item" data-chunk-idx="${{idx}}">
      <div class="meta">
        <span><b>${{item.ticker}}</b> &middot; ${{item.sub_type ?? ""}} ${{item.publication_date ? "&middot; " + item.publication_date : ""}} ${{item.ai_category_label ? "&middot; " + item.ai_category_label : ""}}</span>
        <span>${{scoreBadges(item)}}</span>
      </div>
      <div class="snippet">${{item.snippet}}</div>
      <div class="snippet-hint">Click to view full chunk &rarr;</div>
    </li>
  `;
  }}).join("") + `</ul>`;
}}

function renderTypePanel(ct) {{
  return `
    <section>
      <h2>Full Content Information</h2>
      <div class="stat-row">
        <div class="stat-card"><div class="num">${{fmt(ct.doc_count)}}</div><div class="label">Total documents</div></div>
        <div class="stat-card"><div class="num">${{fmt(ct.chunk_count)}}</div><div class="label">Financial embedded chunks</div></div>
      </div>
      <details class="desc">
        <summary>Coverage, source, considerations &amp; known issues</summary>
        <div class="desc-body">
          <h4>Source</h4><p>${{ct.description.source}}</p>
          <h4>Coverage</h4><p>${{ct.description.coverage}}</p>
          <h4>Considerations</h4><p>${{ct.description.considerations}}</p>
          <h4>Known issues</h4><p>${{ct.description.known_issues}}</p>
        </div>
      </details>
    </section>

    <section>
      <h2>${{ct.stage_1_title}}</h2>
      ${{ct.stage_1_pruned ? `<div class="flag-note">See <a href="2_Adaptive_Rag_Classification.html" target="_blank">Adaptive RAG Classification</a> for how documents are narrowed down across rounds before every remaining chunk is classified.</div>` : ""}}
      ${{ct.sub_type_scope ? `<div class="flag-note">Scoped to ${{ct.sub_type_scope.join(", ")}} only, to keep this comparison consistent across tickers.</div>` : ""}}
      <div class="retrieval-sentence">
        Dynamic Retrieval considered <b>${{fmt(ct.considered)}}</b> chunks in total and identified <b>${{fmt(ct.ai_relevant)}}</b> as AI relevant.
        <div class="pct-line">${{pct(ct.ai_relevant, ct.considered)}}% of considered chunks were flagged AI relevant</div>
      </div>
      <div class="ticker-table-wrap">
        <h3 class="ticker-table-title">% Chunks AI Relevant by Ticker</h3>
        ${{renderTickerTable(ct.by_ticker)}}
      </div>
    </section>

    <section>
      <h2>${{report.stage_2_title}} <span class="stage-2-note">*${{report.stage_2_note}}</span></h2>
      <div class="metric-grid">
        <div class="metric-block">
          <h3>Materiality &mdash; Top 5</h3>
          ${{renderRankList(ct.materiality_top)}}
        </div>
        <div class="metric-block">
          <h3>Materiality &mdash; Bottom 5</h3>
          ${{renderRankList(ct.materiality_bottom)}}
        </div>
        <div class="metric-block">
          <h3>Saliency &mdash; Top 5</h3>
          ${{renderRankList(ct.saliency_top)}}
        </div>
        <div class="metric-block">
          <h3>Saliency &mdash; Bottom 5</h3>
          ${{renderRankList(ct.saliency_bottom)}}
        </div>
      </div>
    </section>
  `;
}}

const tabbar = document.getElementById("type-tabbar");
const panelsContainer = document.getElementById("type-panels");

report.content_types.forEach((ct, i) => {{
  const btn = document.createElement("button");
  btn.textContent = ct.label;
  btn.className = i === 0 ? "active" : "";
  btn.dataset.i = i;
  tabbar.appendChild(btn);

  const panel = document.createElement("div");
  panel.className = "panel" + (i === 0 ? " active" : "");
  panel.dataset.i = i;
  panel.innerHTML = renderTypePanel(ct);
  panelsContainer.appendChild(panel);
}});

tabbar.addEventListener("click", (e) => {{
  const btn = e.target.closest("button");
  if (!btn) return;
  tabbar.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === btn));
  panelsContainer.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.dataset.i === btn.dataset.i));
}});

const modal = document.getElementById("chunk-modal");
const modalMeta = document.getElementById("modal-meta-text");
const modalBody = document.getElementById("modal-body");

function openChunkModal(item) {{
  modalMeta.innerHTML = `
    <b>${{item.ticker}}</b> &middot; ${{item.sub_type ?? ""}}
    ${{item.publication_date ? "&middot; " + item.publication_date : ""}}
    ${{item.ai_category_label ? "&middot; " + item.ai_category_label : ""}}
    &middot; ${{scoreBadges(item)}}
  `;
  modalBody.innerHTML = marked.parse(item.full_text);
  modal.hidden = false;
}}

function closeChunkModal() {{
  modal.hidden = true;
}}

panelsContainer.addEventListener("click", (e) => {{
  const li = e.target.closest(".rank-item");
  if (!li) return;
  const item = chunkRegistry[Number(li.dataset.chunkIdx)];
  if (item) openChunkModal(item);
}});

document.getElementById("modal-close").addEventListener("click", closeChunkModal);
modal.addEventListener("click", (e) => {{ if (e.target === modal) closeChunkModal(); }});
document.addEventListener("keydown", (e) => {{ if (e.key === "Escape") closeChunkModal(); }});
</script>
</body>
</html>
"""


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "1_Stage_1_Overview.html"

    print("querying database...")
    report = build_report()

    print("rendering HTML...")
    html = HTML_TEMPLATE.format(report_json=json.dumps(report))
    output_path.write_text(html)

    print(f"wrote {output_path} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
