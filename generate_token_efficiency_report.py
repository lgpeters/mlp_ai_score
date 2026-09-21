"""Generates a static HTML report on token efficiency / cost across the
two classification stages:

  Stage 1 - Dynamic RAG pruning (Haiku 4.5): round-based candidate
  retrieval over SEC filings and earnings-call transcripts, narrowing the
  active document set round over round until only a handful of
  high-signal companies/documents remain in the final rounds.

  Stage 2 - Focused materiality/saliency classification (Sonnet 5): one
  batch per (company, content type), every chunk that survived Stage 1's
  gate, with prompt caching scoped per pair.

Pulls real token usage from chunk_batch_requests (populated by
RECORD_USAGE_SQL in the classification modules) and, for Stage 1's
per-round chunk counts, from chunk_classifications.round (tracked for
every round regardless of whether token-cost tracking was live yet).

Usage: python3 generate_token_efficiency_report.py [output_path]
Default output: token_efficiency_report.html (repo root).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from classification.second_pass.run_second_pass import _system_prompt, get_companies
from shared.db import get_connection

REPO_ROOT = Path(__file__).resolve().parent

# Real per-million-token batch pricing (see platform.claude.com/docs/en/about-claude/pricing).
PRICES = {
    "claude-haiku-4-5-20251001": {"input": 0.50, "output": 2.50, "cache_write": 1.00, "cache_read": 0.05},
    "claude-sonnet-5": {"input": 1.00, "output": 5.00, "cache_write": 2.00, "cache_read": 0.10},
}

# The one canceled batch (msgbatch_017S5ukBiQDxgsB9erC1Ejvj) whose tracking
# rows were deleted before its real "ended" status was discovered -- its
# usage was recovered from the console-downloaded results .jsonl instead of
# the DB, so it's hardcoded here as a one-off rather than queried.
CANCELED_BATCH_USAGE = {
    "batch_id": "msgbatch_017S5ukBiQDxgsB9erC1Ejvj",
    "model": "claude-sonnet-5",
    "succeeded_requests": 65,
    "canceled_requests": 1,
    "input_tokens": 55148,
    "output_tokens": 51471,
    "cache_creation_input_tokens": 102596,
    "cache_read_input_tokens": 32164,
}

TICKERS = ["ADBE", "CAT", "DELL", "INTC", "MU", "NVDA", "PG", "TEAM", "WM"]
TICKER_COLORS = {
    "ADBE": "#a8560f", "CAT": "#c98a2c", "DELL": "#3c6e8f", "INTC": "#2e6b3e",
    "MU": "#7a4fa0", "NVDA": "#5a8f3c", "PG": "#b3352b", "TEAM": "#4a4a86", "WM": "#8a8a3c",
}

ROUND_CHUNKS_SQL = """
    select cc.round, co.ticker, count(*)
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where c.type = %(content_type)s and cc.round is not null
    group by cc.round, co.ticker
    order by cc.round, co.ticker
"""

STAGE_1_UNTRACKED_SQL = """
    select c.type, cc.round
    from chunk_batch_requests cbr
    join content_index ci on ci.chunk_id = cbr.chunk_ids[1]
    join content c using (content_id)
    left join chunk_classifications cc on cc.chunk_id = cbr.chunk_ids[1]
    where cbr.model is null and c.type in ('sec_filing', 'transcripts')
    group by c.type, cc.round
    order by c.type, cc.round
"""

STAGE_1_USAGE_SQL = """
    select sum(input_tokens), sum(output_tokens)
    from chunk_batch_requests
    where model = 'claude-haiku-4-5-20251001'
"""

STAGE_2_USAGE_BY_TYPE_SQL = """
    select c.type,
           sum(cbr.input_tokens), sum(cbr.output_tokens),
           sum(cbr.cache_creation_input_tokens), sum(cbr.cache_read_input_tokens),
           count(*)
    from chunk_batch_requests cbr
    join content_index ci on ci.chunk_id = cbr.chunk_ids[1]
    join content c using (content_id)
    where cbr.model = 'claude-sonnet-5'
    group by c.type
"""

CONTENT_TYPE_LABELS = {"hackernews": "News", "sec_filing": "SEC Filings", "transcripts": "Transcripts"}


def cost(model: str, input_tokens: int, output_tokens: int,
         cache_write: int = 0, cache_read: int = 0) -> float:
    p = PRICES[model]
    return (
        (input_tokens or 0) * p["input"]
        + (output_tokens or 0) * p["output"]
        + (cache_write or 0) * p["cache_write"]
        + (cache_read or 0) * p["cache_read"]
    ) / 1_000_000


def fetch_round_chart(content_type: str, company_names: dict[str, str]) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ROUND_CHUNKS_SQL, {"content_type": content_type})
            rows = cur.fetchall()

    by_round: dict[int, dict[str, int]] = {}
    for round_no, ticker, n in rows:
        by_round.setdefault(round_no, {})[ticker] = n

    rounds = sorted(by_round)
    final_note = None
    if rounds:
        final_round = rounds[-1]
        final_companies = sorted(by_round[final_round], key=lambda t: -by_round[final_round][t])
        names = [company_names.get(t, t) for t in final_companies]
        if len(names) == 1:
            final_note = f"By the last round, {names[0]} was the only company left under review."
        else:
            final_note = f"By the last round, only {', '.join(names[:-1])} and {names[-1]} were still left under review."

    return {
        "rounds": rounds,
        "series": [{"round": r, "by_ticker": by_round[r]} for r in rounds],
        "max_total": max(sum(by_round[r].values()) for r in rounds) if rounds else 0,
        "final_note": final_note,
    }


def fetch_stage_1(company_names: dict[str, str]) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(STAGE_1_USAGE_SQL)
            in_tok, out_tok = cur.fetchone()

            cur.execute(STAGE_1_UNTRACKED_SQL)
            untracked = cur.fetchall()

    tracked_cost = cost("claude-haiku-4-5-20251001", in_tok, out_tok)
    untracked_rounds = sorted({r for _, r in untracked if r is not None})

    return {
        "input_tokens": in_tok or 0,
        "output_tokens": out_tok or 0,
        "tracked_cost": round(tracked_cost, 2),
        "untracked_note": (
            f"Cost tracking was switched on partway through this stage, so the earliest rounds "
            f"(rounds {untracked_rounds[0]}-{untracked_rounds[-1]}) aren't included in the ${tracked_cost:.2f} "
            f"figure above -- the real cost is a little higher. This has no effect on the charts below, "
            f"which count every round from the start."
        ),
        "sec_chart": fetch_round_chart("sec_filing", company_names),
        "transcripts_chart": fetch_round_chart("transcripts", company_names),
    }


def fetch_stage_2() -> dict:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(STAGE_2_USAGE_BY_TYPE_SQL)
            rows = cur.fetchall()

    by_type = []
    tracked_total_cost = 0.0
    for content_type, in_tok, out_tok, cache_w, cache_r, n_requests in rows:
        c = cost("claude-sonnet-5", in_tok, out_tok, cache_w, cache_r)
        tracked_total_cost += c
        by_type.append({
            "type": content_type,
            "label": CONTENT_TYPE_LABELS.get(content_type, content_type),
            "requests": n_requests,
            "input_tokens": in_tok or 0,
            "output_tokens": out_tok or 0,
            "cache_hit_tokens": cache_r or 0,
            "cache_write_tokens": cache_w or 0,
            "cost": round(c, 2),
        })
    by_type.sort(key=lambda r: r["type"])

    canceled_cost = cost(
        CANCELED_BATCH_USAGE["model"],
        CANCELED_BATCH_USAGE["input_tokens"],
        CANCELED_BATCH_USAGE["output_tokens"],
        CANCELED_BATCH_USAGE["cache_creation_input_tokens"],
        CANCELED_BATCH_USAGE["cache_read_input_tokens"],
    )

    companies = {ticker: (cid, ticker, name) for cid, ticker, name in get_companies()}
    example_ticker = "CAT"
    cid, ticker, name = companies[example_ticker]
    example_prompts = {
        label: _system_prompt(label, ticker, name)
        for label in ("News", "SEC", "Transcripts")
    }

    return {
        "by_type": by_type,
        "tracked_cost": round(tracked_total_cost, 2),
        "canceled_batch": {
            "batch_id": CANCELED_BATCH_USAGE["batch_id"],
            "succeeded_requests": CANCELED_BATCH_USAGE["succeeded_requests"],
            "canceled_requests": CANCELED_BATCH_USAGE["canceled_requests"],
            "cost": round(canceled_cost, 2),
            "note": "This batch was stopped early because it looked stuck -- it wasn't; 65 of its 66 "
                    "requests had actually already finished. Its cost is still counted here, even "
                    "though the results themselves couldn't be recovered afterwards and those items "
                    "simply had to be processed again.",
        },
        "total_cost": round(tracked_total_cost + canceled_cost, 2),
        "example_ticker": example_ticker,
        "example_prompts": example_prompts,
    }


def build_report() -> dict:
    company_names = {ticker: name for _, ticker, name in get_companies()}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker_colors": TICKER_COLORS,
        "company_names": company_names,
        "stage_1": fetch_stage_1(company_names),
        "stage_2": fetch_stage_2(),
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Token Efficiency Report</title>
<style>
  :root {{
    --bg: #f7f7f5; --panel: #ffffff; --border: #e2e0da; --text: #24231f;
    --muted: #6b6a64; --accent: #a8560f; --accent-soft: #f4e6d8; --code-bg: #f1efe9;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2.5rem 1.5rem 4rem; background: var(--bg); color: var(--text);
    font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; line-height: 1.55;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  h1 {{ font-size: 1.9rem; margin: 0 0 0.25rem; letter-spacing: -0.01em; }}
  .subtitle {{ color: var(--muted); font-size: 0.95rem; margin-bottom: 2.5rem; }}
  .subtitle code {{ font-family: "SF Mono", Menlo, monospace; font-size: 0.85em; }}

  section.stage {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 1.75rem 2rem; margin-bottom: 2.5rem;
  }}
  h2 {{ font-size: 1.3rem; margin: 0 0 0.3rem; }}
  h2 .model {{ font-size: 0.85rem; color: var(--muted); font-weight: 400; font-family: -apple-system, sans-serif; }}
  .stage-desc {{ color: var(--text); font-size: 0.98rem; margin: 0.5rem 0 1.2rem; }}

  .cost-card {{
    display: inline-flex; align-items: baseline; gap: 0.5rem; background: var(--accent-soft);
    border-radius: 8px; padding: 0.7rem 1.1rem; margin-bottom: 1rem;
  }}
  .cost-card .amount {{ font-family: "SF Mono", Menlo, monospace; font-size: 1.5rem; font-weight: 700; color: var(--accent); }}
  .cost-card .cost-label {{ font-family: -apple-system, sans-serif; font-size: 0.82rem; color: var(--muted); }}

  .flag-note {{
    font-family: -apple-system, sans-serif; font-size: 0.78rem; color: var(--muted);
    background: var(--code-bg); border-left: 3px solid var(--accent); border-radius: 4px;
    padding: 0.6rem 0.85rem; margin-bottom: 1.2rem;
  }}

  h3 {{ font-family: -apple-system, sans-serif; font-size: 0.85rem; margin: 1.6rem 0 0.6rem; color: var(--text); }}

  .legend {{ display: flex; flex-wrap: wrap; gap: 0.6rem 1rem; font-family: -apple-system, sans-serif; font-size: 0.75rem; margin-bottom: 0.6rem; color: var(--muted); }}
  .legend .swatch {{ display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 2px; margin-right: 0.3rem; vertical-align: middle; }}

  .chart-wrap {{ overflow-x: auto; }}
  .axis-caption {{ font-family: -apple-system, sans-serif; font-size: 0.72rem; color: var(--muted); margin-top: 0.3rem; }}
  .final-note {{
    font-family: -apple-system, sans-serif; font-size: 0.85rem; color: var(--text);
    margin-top: 0.7rem; padding: 0.6rem 0.85rem; background: var(--accent-soft); border-radius: 6px;
  }}
  .final-note-badge {{
    display: inline-block; background: var(--accent); color: white; font-size: 0.68rem;
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.45rem;
    border-radius: 4px; margin-right: 0.4rem;
  }}

  table.usage-table {{
    width: 100%; border-collapse: collapse; font-family: -apple-system, sans-serif; font-size: 0.82rem;
    margin-top: 0.5rem;
  }}
  table.usage-table th {{
    text-align: right; padding: 0.5rem 0.7rem; background: var(--code-bg); color: var(--muted);
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.03em; font-weight: 600;
  }}
  table.usage-table th:first-child, table.usage-table td:first-child {{ text-align: left; }}
  table.usage-table td {{ text-align: right; padding: 0.45rem 0.7rem; border-top: 1px solid var(--border); font-family: "SF Mono", Menlo, monospace; }}
  table.usage-table tfoot td {{ font-weight: 700; border-top: 2px solid var(--border); }}

  .prompt-picker {{ margin: 1rem 0 0.6rem; }}
  .prompt-picker select {{
    font-family: -apple-system, sans-serif; font-size: 0.85rem; padding: 0.4rem 0.6rem;
    border: 1px solid var(--border); border-radius: 6px; background: var(--panel); color: var(--text);
  }}
  pre.prompt-box {{
    background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 1rem 1.2rem; font-family: "SF Mono", Menlo, monospace; font-size: 0.78rem;
    white-space: pre-wrap; word-wrap: break-word; max-height: 420px; overflow-y: auto; margin: 0;
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Token Efficiency Report</h1>
  <div class="subtitle">Generated <code id="generated-at"></code>. Real token usage and cost, pulled live from batch request tracking.</div>

  <section class="stage" id="stage-1"></section>
  <section class="stage" id="stage-2"></section>
</div>

<script id="report-data" type="application/json">{report_json}</script>
<script>
const report = JSON.parse(document.getElementById("report-data").textContent);
document.getElementById("generated-at").textContent = new Date(report.generated_at).toLocaleString();

function fmt(n) {{ return (n ?? 0).toLocaleString(); }}
function money(n) {{ return "$" + (n ?? 0).toFixed(2); }}

const TICKER_ORDER = {ticker_order_json};
const COLORS = report.ticker_colors;

function renderLegend(tickersUsed) {{
  return `<div class="legend">` + tickersUsed.map(t =>
    `<span><span class="swatch" style="background:${{COLORS[t]}}"></span>${{report.company_names[t] || t}}</span>`
  ).join("") + `</div>`;
}}

function renderStackedBarChart(chart, title) {{
  if (!chart.rounds.length) {{
    return `<h3>${{title}}</h3><div class="flag-note">No results yet for this stage.</div>`;
  }}

  const tickersUsed = TICKER_ORDER.filter(t => chart.series.some(s => t in s.by_ticker));
  const barW = 46, gap = 16, chartH = 260, labelH = 30, leftAxisW = 48;
  const w = leftAxisW + chart.series.length * (barW + gap) + gap;
  const h = chartH + labelH + 10;
  const scale = chartH / (chart.max_total || 1);
  const lastIdx = chart.series.length - 1;

  let highlight = "";
  let bars = "";
  chart.series.forEach((s, i) => {{
    const x = leftAxisW + gap + i * (barW + gap);

    if (i === lastIdx) {{
      highlight = `<rect x="${{x - 6}}" y="-6" width="${{barW + 12}}" height="${{chartH + 12}}" rx="8" fill="var(--accent-soft)" opacity="0.6"/>`;
    }}

    let yCursor = chartH;
    let segs = "";
    tickersUsed.forEach(ticker => {{
      const n = s.by_ticker[ticker] || 0;
      if (n === 0) return;
      const segH = n * scale;
      yCursor -= segH;
      segs += `<rect x="${{x}}" y="${{yCursor}}" width="${{barW}}" height="${{segH}}" fill="${{COLORS[ticker]}}">
        <title>${{report.company_names[ticker] || ticker}} — Round ${{s.round}} — ${{n.toLocaleString()}} excerpts reviewed</title>
      </rect>`;
    }});
    const total = Object.values(s.by_ticker).reduce((a, b) => a + b, 0);
    bars += segs;
    bars += `<text x="${{x + barW / 2}}" y="${{chartH + 19}}" text-anchor="middle" font-size="12" font-family="-apple-system,sans-serif" fill="var(--muted)">${{s.round}}</text>`;
    bars += `<text x="${{x + barW / 2}}" y="${{yCursor - 6}}" text-anchor="middle" font-size="10" font-family="-apple-system,sans-serif" fill="var(--text)">${{total.toLocaleString()}}</text>`;
  }});

  // y-axis gridlines (4 ticks)
  let grid = "";
  for (let i = 0; i <= 4; i++) {{
    const val = Math.round(chart.max_total * i / 4);
    const y = chartH - val * scale;
    grid += `<line x1="${{leftAxisW}}" y1="${{y}}" x2="${{w}}" y2="${{y}}" stroke="var(--border)" stroke-width="1"/>`;
    grid += `<text x="${{leftAxisW - 6}}" y="${{y + 3}}" text-anchor="end" font-size="10" font-family="-apple-system,sans-serif" fill="var(--muted)">${{val.toLocaleString()}}</text>`;
  }}

  return `
    <h3>${{title}}</h3>
    ${{renderLegend(tickersUsed)}}
    <div class="chart-wrap">
      <svg width="${{w}}" height="${{h}}" viewBox="0 0 ${{w}} ${{h}}">
        ${{highlight}}
        ${{grid}}
        ${{bars}}
      </svg>
      <div class="axis-caption">Round of review (left to right = later, more narrowed-down rounds)</div>
    </div>
    ${{chart.final_note ? `<div class="final-note"><span class="final-note-badge">Narrowed to the end</span> ${{chart.final_note}}</div>` : ""}}
  `;
}}

function renderStage1() {{
  const s1 = report.stage_1;
  document.getElementById("stage-1").innerHTML = `
    <h2>Stage 1 &mdash; Wide Scan <span class="model">(AI model: Haiku 4.5)</span></h2>
    <p class="stage-desc">A fast, cheap first read of everything we collected, run in repeated rounds. Each round, companies whose material was no longer showing real AI relevance were dropped, so later rounds cover fewer and fewer companies &mdash; concentrating the (more expensive) work in Stage 2 on only the material that's actually worth a closer look.</p>
    <div class="cost-card"><span class="amount">${{money(s1.tracked_cost)}}</span><span class="cost-label">cost so far</span></div>
    <div class="flag-note">${{s1.untracked_note}}</div>
    ${{renderStackedBarChart(s1.sec_chart, "SEC Filings &mdash; excerpts reviewed per round, by company")}}
    ${{renderStackedBarChart(s1.transcripts_chart, "Earnings Call Transcripts &mdash; excerpts reviewed per round, by company")}}
  `;
}}

function renderPromptPicker(examplePrompts, ticker) {{
  const labels = Object.keys(examplePrompts);
  const options = labels.map((l, i) => `<option value="${{l}}" ${{i === 0 ? "selected" : ""}}>${{l}}</option>`).join("");
  const name = report.company_names[ticker] || ticker;
  return `
    <h3>What the AI actually reads &mdash; example for ${{name}}</h3>
    <p class="stage-desc">The instructions below are reused for every excerpt from the same company and content type, so they only need to be sent to the AI once per hour rather than every single time &mdash; that reuse is what "caching" refers to in the cost table below.</p>
    <div class="prompt-picker">
      <select id="prompt-select">${{options}}</select>
    </div>
    <pre class="prompt-box" id="prompt-box">${{examplePrompts[labels[0]]}}</pre>
  `;
}}

function renderUsageTable(byType) {{
  const rows = byType.map(row => `
    <tr>
      <td>${{row.label}}</td>
      <td>${{fmt(row.requests)}}</td>
      <td>${{fmt(row.input_tokens)}}</td>
      <td>${{fmt(row.cache_hit_tokens)}}</td>
      <td>${{fmt(row.output_tokens)}}</td>
      <td>${{money(row.cost)}}</td>
    </tr>
  `).join("");
  const totals = byType.reduce((acc, r) => ({{
    requests: acc.requests + r.requests, input: acc.input + r.input_tokens,
    cache: acc.cache + r.cache_hit_tokens, output: acc.output + r.output_tokens, cost: acc.cost + r.cost,
  }}), {{requests: 0, input: 0, cache: 0, output: 0, cost: 0}});
  return `
    <table class="usage-table">
      <thead><tr><th>Content Type</th><th>Batches Sent</th><th>Text Sent to AI</th><th>Reused from Cache</th><th>AI's Written Response</th><th>Cost</th></tr></thead>
      <tbody>${{rows}}</tbody>
      <tfoot><tr><td>Total</td><td>${{fmt(totals.requests)}}</td><td>${{fmt(totals.input)}}</td><td>${{fmt(totals.cache)}}</td><td>${{fmt(totals.output)}}</td><td>${{money(totals.cost)}}</td></tr></tfoot>
    </table>
    <div class="axis-caption">Figures are in tokens &mdash; roughly, pieces of a word. "Reused from Cache" is text the AI didn't have to be charged full price to re-read.</div>
  `;
}}

function renderStage2() {{
  const s2 = report.stage_2;
  document.getElementById("stage-2").innerHTML = `
    <h2>Stage 2 &mdash; Focused Scoring <span class="model">(AI model: Sonnet 5)</span></h2>
    <p class="stage-desc">A closer, more careful read of only the material that survived Stage 1 &mdash; scoring how material and how positive or negative each excerpt is for the company in question.</p>
    <div class="cost-card"><span class="amount">${{money(s2.total_cost)}}</span><span class="cost-label">total cost, including one run that had to be redone</span></div>
    <div class="flag-note">
      ${{money(s2.canceled_batch.cost)}} of the total above comes from a run that was stopped early and had to be redone. ${{s2.canceled_batch.note}}
    </div>
    ${{renderPromptPicker(s2.example_prompts, s2.example_ticker)}}
    <h3>Usage by content type</h3>
    ${{renderUsageTable(s2.by_type)}}
  `;

  document.getElementById("prompt-select").addEventListener("change", (e) => {{
    document.getElementById("prompt-box").textContent = s2.example_prompts[e.target.value];
  }});
}}

renderStage1();
renderStage2();
</script>
</body>
</html>
"""


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "3_Token_Efficiency.html"

    print("querying database...")
    report = build_report()

    print("rendering HTML...")
    html = HTML_TEMPLATE.format(
        report_json=json.dumps(report),
        ticker_order_json=json.dumps(TICKERS),
    )
    output_path.write_text(html)

    print(f"wrote {output_path} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
