"""Generates a static HTML scatter chart: VADER compound score (blind,
context-free sentiment baseline) on the x-axis vs. the second pass's
AI-conditioned saliency score on the y-axis, colored by content type.

The point of the comparison: VADER has zero concept of "AI" or "company"
-- it's a generic lexicon scorer. If AI-conditioned saliency just tracked
generic positive/negative language, the two would line up closely. Real
per-type correlation coefficients are computed and shown alongside the
chart so the comparison isn't just visual.

Usage: python3 generate_vader_scatter_report.py [output_path]
Default output: vader_saliency_scatter.html (repo root).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.db import get_connection
from shared.storage import get_chunk_content

REPO_ROOT = Path(__file__).resolve().parent

CONTENT_TYPE_LABELS = {"sec_filing": "SEC Filings", "transcripts": "Transcripts", "hackernews": "News"}
CONTENT_TYPE_COLORS = {"sec_filing": "#a8560f", "transcripts": "#3c6e8f", "hackernews": "#2e6b3e"}

CONTENT_TYPE_REASONING = {
    "transcripts": "Management language is rarely negative regardless of the implicit business impact.",
    "hackernews": 'Positive language is not positive news (e.g. "Nvidia AGREES to export controls").',
    "sec_filing": "Language here generally always relates directly to business impact in a considered manner.",
}

# Explicit belt-and-suspenders scope, matching the second pass's own scope
# (ai_saliency_score only ever gets set for chunks the second pass actually
# ran on, which already implies this -- but stating it explicitly here
# means the correlation table can't silently drift if that ever changes).
SUB_TYPE_FILTERS = {
    "sec_filing": ["10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "6-K", "6-K/A"],
    "transcripts": ["earnings_call"],
    "hackernews": None,
}

POINTS_SQL = """
    select cc.vader_compound, cc.ai_saliency_score, c.type, co.ticker,
           ci.content_id, ci.start_offset, ci.end_offset, c.storage_path, c.sub_type, c.publication_date
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where cc.vader_compound is not null and cc.ai_saliency_score is not null
      and c.type = %(content_type)s
      and (%(sub_types)s::text[] is null or c.sub_type = any(%(sub_types)s))
"""


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = (var_x * var_y) ** 0.5
    return cov / denom if denom else 0.0


def fetch_points() -> list[dict]:
    points = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            for content_type, sub_types in SUB_TYPE_FILTERS.items():
                cur.execute(POINTS_SQL, {"content_type": content_type, "sub_types": sub_types})
                for row in cur.fetchall():
                    (vader, saliency, ctype, ticker, content_id, start, end,
                     storage_path, sub_type, pub_date) = row
                    gap = abs(float(vader) - saliency / 3)
                    points.append({
                        "x": float(vader), "y": saliency, "type": ctype, "ticker": ticker, "gap": gap,
                        "_content_id": content_id, "_start": start, "_end": end,
                        "_storage_path": storage_path, "_sub_type": sub_type,
                        "_pub_date": pub_date.isoformat() if pub_date else None,
                    })
    return points


def build_example(p: dict, snippet_chars: int = 260) -> dict:
    full_text = get_chunk_content(p["_content_id"], p["type"], p["_storage_path"], p["_start"], p["_end"]).strip()
    flat = full_text.replace("\n", " ")
    snippet = flat if len(flat) <= snippet_chars else flat[:snippet_chars].rsplit(" ", 1)[0] + "..."
    return {
        "ticker": p["ticker"],
        "type": p["type"],
        "sub_type": p["_sub_type"],
        "publication_date": p["_pub_date"],
        "vader": round(p["x"], 3),
        "saliency": p["y"],
        "gap": round(p["gap"], 3),
        "snippet": snippet,
        "full_text": full_text,
    }


def build_report() -> dict:
    points = fetch_points()

    by_type: dict[str, list[dict]] = {}
    for p in points:
        by_type.setdefault(p["type"], []).append(p)

    stats = []
    for content_type, pts in by_type.items():
        xs = [p["x"] for p in pts]
        ys = [float(p["y"]) for p in pts]
        sub_types = SUB_TYPE_FILTERS.get(content_type)
        stats.append({
            "type": content_type,
            "label": CONTENT_TYPE_LABELS.get(content_type, content_type),
            "scope": ", ".join(sub_types) if sub_types else "all sub-types",
            "n": len(pts),
            "correlation": round(pearson(xs, ys), 3),
            "reasoning": CONTENT_TYPE_REASONING.get(content_type, ""),
        })
    stats.sort(key=lambda s: s["type"])

    all_xs = [p["x"] for p in points]
    all_ys = [float(p["y"]) for p in points]
    overall_correlation = round(pearson(all_xs, all_ys), 3)

    by_gap_desc = sorted(points, key=lambda p: -p["gap"])
    biggest_gaps = [build_example(p) for p in by_gap_desc[:2]]

    public_points = [{"x": p["x"], "y": p["y"], "type": p["type"], "ticker": p["ticker"]} for p in points]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "points": public_points,
        "stats": stats,
        "overall_correlation": overall_correlation,
        "overall_n": len(points),
        "type_labels": CONTENT_TYPE_LABELS,
        "type_colors": CONTENT_TYPE_COLORS,
        "biggest_gaps": biggest_gaps,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VADER vs Saliency</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
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
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.9rem; margin: 0 0 0.25rem; letter-spacing: -0.01em; }}
  .subtitle {{ color: var(--muted); font-size: 0.95rem; margin-bottom: 1.8rem; max-width: 62ch; }}
  .subtitle code {{ font-family: "SF Mono", Menlo, monospace; font-size: 0.85em; }}

  section.panel {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 1.75rem 2rem; margin-bottom: 1.5rem;
  }}

  .stat-row {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.4rem; }}
  .stat-card {{
    flex: 1; min-width: 150px; background: var(--code-bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 0.8rem 1rem; font-family: -apple-system, sans-serif;
  }}
  .stat-card .swatch {{ display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%; margin-right: 0.4rem; }}
  .stat-card .corr {{
    font-family: "SF Mono", Menlo, monospace; font-size: 1.4rem; font-weight: 700; color: var(--text);
    display: block; margin-top: 0.2rem;
  }}
  .stat-card .label {{ font-size: 0.78rem; color: var(--muted); }}
  .stat-card .n {{ font-size: 0.7rem; color: var(--muted); }}

  .legend {{ display: flex; gap: 1.2rem; flex-wrap: wrap; font-family: -apple-system, sans-serif; font-size: 0.82rem; margin-bottom: 0.8rem; }}
  .legend .swatch {{ display: inline-block; width: 0.65rem; height: 0.65rem; border-radius: 50%; margin-right: 0.4rem; vertical-align: middle; }}

  .chart-wrap {{ overflow-x: auto; }}
  .axis-caption {{ font-family: -apple-system, sans-serif; font-size: 0.75rem; color: var(--muted); margin-top: 0.6rem; }}

  h2 {{ font-size: 1.05rem; margin: 0 0 0.9rem; }}
  table.corr-table {{
    width: 100%; border-collapse: collapse; font-family: -apple-system, sans-serif; font-size: 0.85rem;
  }}
  table.corr-table th {{
    text-align: left; padding: 0.5rem 0.7rem; background: var(--code-bg); color: var(--muted);
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.03em; font-weight: 600;
  }}
  table.corr-table th.num, table.corr-table td.num {{ text-align: right; }}
  table.corr-table td {{ padding: 0.55rem 0.7rem; border-top: 1px solid var(--border); }}
  table.corr-table .type-cell {{ display: flex; align-items: center; gap: 0.5rem; font-weight: 600; }}
  table.corr-table .swatch {{ display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%; }}
  table.corr-table .corr-val {{ font-family: "SF Mono", Menlo, monospace; font-weight: 700; }}
  table.corr-table .scope-cell {{ color: var(--muted); font-size: 0.8rem; }}
  table.corr-table .reasoning-cell {{ font-style: italic; color: var(--text); max-width: 26ch; }}
  table.corr-table tfoot td {{ font-weight: 700; border-top: 2px solid var(--border); }}

  .example-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }}
  @media (max-width: 640px) {{ .example-grid {{ grid-template-columns: 1fr; }} }}
  .example-card {{
    background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.2rem;
    cursor: pointer; transition: border-color 0.15s, box-shadow 0.15s;
  }}
  .example-card:hover {{ border-color: var(--accent); box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .example-card .click-hint {{
    margin-top: 0.5rem; font-family: -apple-system, sans-serif; font-size: 0.7rem; color: var(--accent);
  }}
  .example-card .example-title {{
    font-family: -apple-system, sans-serif; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--accent); font-weight: 700; margin-bottom: 0.5rem;
  }}
  .example-card .example-meta {{
    font-family: -apple-system, sans-serif; font-size: 0.75rem; color: var(--muted); margin-bottom: 0.6rem;
    display: flex; flex-wrap: wrap; gap: 0.5rem;
  }}
  .example-card .score-badge {{
    font-family: "SF Mono", Menlo, monospace; font-weight: 700; padding: 0.05rem 0.4rem;
    border-radius: 4px; background: var(--panel); border: 1px solid var(--border);
  }}
  .example-card .example-text {{ font-size: 0.92rem; }}

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
</style>
</head>
<body>
<div class="wrap">
  <h1>VADER vs. AI-Conditioned Saliency</h1>
  <div class="subtitle">Generated <code id="generated-at"></code>. VADER is a blind, generic sentiment scorer with no concept of "AI" or "company" &mdash; comparing it against the second pass's AI-conditioned saliency score shows whether that score is tracking something more specific than plain positive/negative language.</div>

  <section class="panel">
    <h2>Correlation by content type</h2>
    <table class="corr-table" id="corr-table"></table>
    <div class="axis-caption">Each row is scoped to exactly the same sub-types the second pass itself scored -- Transcripts to earnings_call only, SEC to 10-K/10-Q/20-F/6-K only (excluding 8-K/S-4, which the second pass never ran on) -- so the correlation for each type is computed over the same population as its chart points, not a broader or narrower one.</div>
  </section>

  <section class="panel">
    <div class="stat-row" id="stat-row"></div>
    <div class="legend" id="legend"></div>
    <div class="chart-wrap">
      <svg id="scatter" viewBox="0 0 720 480" preserveAspectRatio="xMidYMid meet" style="width:100%; height:auto; min-width:560px"></svg>
    </div>
    <div class="axis-caption">x-axis: VADER compound score (-1 to +1, generic sentiment). y-axis: AI-conditioned saliency score (-3 to +3), jittered slightly for visibility since it only takes whole-number values.</div>
  </section>

  <section class="panel">
    <h2>Biggest gaps</h2>
    <p class="axis-caption" style="margin-top:0; margin-bottom:1rem;">Gap = |VADER compound &minus; (saliency &divide; 3)|, i.e. both scores put on the same -1 to +1 scale before comparing.</p>
    <div class="example-grid" id="example-grid"></div>
  </section>
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

document.getElementById("stat-row").innerHTML = `
  <div class="stat-card">
    <span class="label">Overall correlation</span>
    <span class="corr">${{report.overall_correlation.toFixed(3)}}</span>
    <span class="n">n=${{report.overall_n.toLocaleString()}}</span>
  </div>
` + report.stats.map(s => `
  <div class="stat-card">
    <span class="label"><span class="swatch" style="background:${{report.type_colors[s.type]}}"></span>${{s.label}}</span>
    <span class="corr">${{s.correlation.toFixed(3)}}</span>
    <span class="n">n=${{s.n.toLocaleString()}}</span>
  </div>
`).join("");

document.getElementById("legend").innerHTML = report.stats.map(s =>
  `<span><span class="swatch" style="background:${{report.type_colors[s.type]}}"></span>${{s.label}}</span>`
).join("");

const corrRows = report.stats.map(s => `
  <tr>
    <td><span class="type-cell"><span class="swatch" style="background:${{report.type_colors[s.type]}}"></span>${{s.label}}</span></td>
    <td class="scope-cell">${{s.scope}}</td>
    <td class="num">${{s.n.toLocaleString()}}</td>
    <td class="num corr-val">${{s.correlation.toFixed(3)}}</td>
    <td class="reasoning-cell">${{s.reasoning}}</td>
  </tr>
`).join("");
document.getElementById("corr-table").innerHTML = `
  <thead><tr><th>Content Type</th><th>Scope</th><th class="num">n</th><th class="num">Correlation</th><th>Reasoning</th></tr></thead>
  <tbody>${{corrRows}}</tbody>
  <tfoot><tr><td>Overall</td><td class="scope-cell">all types combined</td><td class="num">${{report.overall_n.toLocaleString()}}</td><td class="num corr-val">${{report.overall_correlation.toFixed(3)}}</td><td></td></tr></tfoot>
`;

const exampleRegistry = [];

function renderExampleCard(title, ex) {{
  const idx = exampleRegistry.length;
  exampleRegistry.push(ex);
  return `
    <div class="example-card" data-example-idx="${{idx}}">
      <div class="example-title">${{title}}</div>
      <div class="example-meta">
        <span><b>${{ex.ticker}}</b></span>
        <span>${{report.type_labels[ex.type]}}${{ex.sub_type ? " / " + ex.sub_type : ""}}</span>
        ${{ex.publication_date ? `<span>${{ex.publication_date}}</span>` : ""}}
        <span class="score-badge">VADER ${{ex.vader}}</span>
        <span class="score-badge">Saliency ${{ex.saliency > 0 ? "+" : ""}}${{ex.saliency}}</span>
        <span class="score-badge">Gap ${{ex.gap}}</span>
      </div>
      <div class="example-text">${{ex.snippet}}</div>
      <div class="click-hint">Click to view full chunk &rarr;</div>
    </div>
  `;
}}

document.getElementById("example-grid").innerHTML =
  renderExampleCard("Biggest gap #1 &mdash; most contradictory", report.biggest_gaps[0]) +
  renderExampleCard("Biggest gap #2 &mdash; also contradictory", report.biggest_gaps[1]);

const modal = document.getElementById("chunk-modal");
const modalMeta = document.getElementById("modal-meta-text");
const modalBody = document.getElementById("modal-body");

function openExampleModal(ex) {{
  modalMeta.innerHTML = `
    <b>${{ex.ticker}}</b> &middot; ${{report.type_labels[ex.type]}}${{ex.sub_type ? " / " + ex.sub_type : ""}}
    ${{ex.publication_date ? "&middot; " + ex.publication_date : ""}}
    &middot; VADER ${{ex.vader}} &middot; Saliency ${{ex.saliency > 0 ? "+" : ""}}${{ex.saliency}} &middot; Gap ${{ex.gap}}
  `;
  modalBody.innerHTML = marked.parse(ex.full_text);
  modal.hidden = false;
}}

document.getElementById("example-grid").addEventListener("click", (e) => {{
  const card = e.target.closest(".example-card");
  if (!card) return;
  openExampleModal(exampleRegistry[Number(card.dataset.exampleIdx)]);
}});

document.getElementById("modal-close").addEventListener("click", () => {{ modal.hidden = true; }});
modal.addEventListener("click", (e) => {{ if (e.target === modal) modal.hidden = true; }});
document.addEventListener("keydown", (e) => {{ if (e.key === "Escape") modal.hidden = true; }});

const svg = document.getElementById("scatter");
const W = 720, H = 480, padL = 50, padR = 20, padT = 20, padB = 50;
const plotW = W - padL - padR, plotH = H - padT - padB;

function xPix(v) {{ return padL + ((v + 1) / 2) * plotW; }}
function yPix(v) {{ return padT + plotH - ((v + 3) / 6) * plotH; }}

let s = "";
// gridlines + axis labels
for (let v = -1; v <= 1; v += 0.5) {{
  const x = xPix(v);
  s += `<line x1="${{x}}" y1="${{padT}}" x2="${{x}}" y2="${{padT + plotH}}" stroke="var(--border)" stroke-width="1"/>`;
  s += `<text x="${{x}}" y="${{padT + plotH + 20}}" text-anchor="middle" font-size="11" font-family="-apple-system,sans-serif" fill="var(--muted)">${{v.toFixed(1)}}</text>`;
}}
for (let v = -3; v <= 3; v++) {{
  const y = yPix(v);
  s += `<line x1="${{padL}}" y1="${{y}}" x2="${{padL + plotW}}" y2="${{y}}" stroke="var(--border)" stroke-width="1"/>`;
  s += `<text x="${{padL - 10}}" y="${{y + 4}}" text-anchor="end" font-size="11" font-family="-apple-system,sans-serif" fill="var(--muted)">${{v}}</text>`;
}}
// axis titles
s += `<text x="${{padL + plotW / 2}}" y="${{H - 8}}" text-anchor="middle" font-size="12" font-family="-apple-system,sans-serif" fill="var(--text)" font-weight="600">VADER compound score</text>`;
s += `<text x="14" y="${{padT + plotH / 2}}" text-anchor="middle" font-size="12" font-family="-apple-system,sans-serif" fill="var(--text)" font-weight="600" transform="rotate(-90 14 ${{padT + plotH / 2}})">AI-conditioned saliency</text>`;

report.points.forEach(p => {{
  const jitter = (Math.random() - 0.5) * 0.7;
  const cx = xPix(p.x);
  const cy = yPix(p.y + jitter);
  const color = report.type_colors[p.type];
  s += `<circle cx="${{cx}}" cy="${{cy}}" r="3.2" fill="${{color}}" opacity="0.45">
    <title>${{p.ticker}} — ${{report.type_labels[p.type]}} — VADER ${{p.x.toFixed(2)}}, saliency ${{p.y}}</title>
  </circle>`;
}});

svg.innerHTML = s;
</script>
</body>
</html>
"""


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "4_AI_Saliency_Proof.html"

    print("querying database...")
    report = build_report()

    print("rendering HTML...")
    html = HTML_TEMPLATE.format(report_json=json.dumps(report))
    output_path.write_text(html)

    print(f"wrote {output_path} ({len(html):,} bytes, {report['overall_n']} points)")


if __name__ == "__main__":
    main()
