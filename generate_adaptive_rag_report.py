"""Generates the "Adaptive RAG Classification" writeup as static HTML,
live from the database -- the procedure summary plus a real per-company,
per-year matrix of SEC annual-report (10-K/20-F) chunks: how much of each
filing the round-based search ever actually looked at, and how much of
that turned out to be genuine AI content.

Usage: python3 generate_adaptive_rag_report.py [output_path]
Default output: 2_Adaptive_Rag_Classification.html (repo root).
"""

import json
import sys
from pathlib import Path

from shared.db import get_connection

REPO_ROOT = Path(__file__).resolve().parent

ANNUAL_REPORT_SUB_TYPES = ["10-K", "10-K/A", "20-F", "20-F/A"]

FILING_MATRIX_SQL = """
    select co.ticker, extract(year from c.publication_date)::int as year,
           count(*) as total,
           count(*) filter (where cc.ai_associated is not null) as classified,
           count(*) filter (where cc.ai_associated > 0) as hits
    from content_index ci
    join content c using (content_id)
    join companies co using (company_id)
    left join chunk_classifications cc using (chunk_id)
    where c.type = 'sec_filing' and c.sub_type = any(%(sub_types)s)
    group by co.ticker, year
    order by co.ticker, year
"""


def fetch_filing_matrix() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(FILING_MATRIX_SQL, {"sub_types": ANNUAL_REPORT_SUB_TYPES})
            rows = cur.fetchall()
    return [
        {"ticker": ticker, "year": year, "total": total, "classified": classified, "hits": hits}
        for ticker, year, total, classified, hits in rows
    ]


def build_report() -> dict:
    filings = fetch_filing_matrix()

    # Company order: by total chunks searched, most first -- matches the
    # original hand-built page's ordering (NVDA led).
    totals_by_ticker: dict[str, int] = {}
    for f in filings:
        totals_by_ticker[f["ticker"]] = totals_by_ticker.get(f["ticker"], 0) + f["classified"]
    company_order = sorted(totals_by_ticker, key=lambda t: -totals_by_ticker[t])

    years = sorted({f["year"] for f in filings})

    return {
        "company_order": company_order,
        "years": years,
        "filings": filings,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Adaptive RAG Classification</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{{
  --bg:#F3F5F6; --panel:#FFFFFF; --ink:#12181F; --muted:#5B6773; --line:#DEE4E8; --kept:#1F9D77;
}}
@media (prefers-color-scheme: dark){{
  :root:not([data-theme="light"]){{ --bg:#10151A; --panel:#171E25; --ink:#E9EEF2; --muted:#8B9AA6; --line:#2A343D; }}
}}
:root[data-theme="dark"]{{ --bg:#10151A; --panel:#171E25; --ink:#E9EEF2; --muted:#8B9AA6; --line:#2A343D; }}
*{{box-sizing:border-box}}
body{{ margin:0; background:var(--bg); color:var(--ink); font-family:"Archivo", system-ui, sans-serif; padding-inline:24px; padding-block:40px 60px; }}
.wrap{{max-width:820px; margin:0 auto; display:flex; flex-direction:column; gap:32px}}
.mono{{font-family:"JetBrains Mono", "SF Mono", monospace; font-variant-numeric:tabular-nums}}

h1{{margin:0; font-size:1.9rem; font-weight:700; letter-spacing:-0.015em; text-wrap:balance}}
h2{{margin:0 0 12px; font-size:1.05rem; font-weight:600; letter-spacing:-0.005em}}

.panel{{background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px clamp(14px,3vw,28px)}}

ol.procedure{{ margin:0; padding-left:1.4em; display:flex; flex-direction:column; gap:10px; font-size:0.98rem; line-height:1.55 }}
ol.procedure li b{{ color:var(--ink) }}
ol.procedure .mono{{ font-size:0.92em; background:var(--bg); border:1px solid var(--line); border-radius:4px; padding:1px 5px }}

.heat-caption{{font-size:0.9rem; font-weight:600; margin-bottom:10px}}
.heatmap-wrap{{overflow-x:auto}}
table.heatmap{{border-collapse:collapse; width:100%; min-width:640px}}
table.heatmap th, table.heatmap td{{padding:0; text-align:center}}
table.heatmap thead th{{
  font-family:"JetBrains Mono", monospace; font-size:11px; font-weight:600; color:var(--muted);
  padding:0 4px 8px; text-transform:uppercase; letter-spacing:0.03em;
}}
table.heatmap thead th:first-child{{text-align:left}}
table.heatmap tbody th{{
  font-family:"JetBrains Mono", monospace; font-size:11.5px; font-weight:500; color:var(--muted);
  text-align:left; padding-right:10px; white-space:nowrap;
}}
table.heatmap td{{padding:2px}}
.heat-cell{{
  display:flex; align-items:center; justify-content:center;
  height:28px; border-radius:5px; font-family:"JetBrains Mono", monospace;
  font-size:11px; font-weight:600;
}}
.heat-empty{{color:var(--line); font-size:11px}}

@media (max-width:520px){{ h1{{font-size:1.5rem}} .panel{{padding-inline:12px}} }}
</style>
</head>
<body>

<div class="wrap">
  <header>
    <h1>Adaptive RAG Classification</h1>
  </header>

  <div class="panel">
    <h2>Procedure</h2>
    <ol class="procedure">
      <li>Embed every content chunk using a lightweight financial embedding model &mdash; <b><span class="mono">FinLang/finance-embeddings-investopedia</span></b>.</li>
      <li>Per document, run 5 key search terms and return the top 5 matches each, with deduplication.</li>
      <li>Batch API call to the Haiku LLM.</li>
      <li>Documents below the hit-rate threshold are dropped &mdash; search continues on the remainder.</li>
      <li>Repeat until all content is exhaustively searched and classified.</li>
    </ol>
  </div>

  <div class="section">
    <h2>SEC Annual Reports example:</h2>
    <div class="panel">
      <div class="heat-caption">% Chunks Searched</div>
      <div class="heatmap-wrap"><table class="heatmap" id="heatClassified"></table></div>
    </div>
    <div style="height:16px"></div>
    <div class="panel">
      <div class="heat-caption">% Chunks AI</div>
      <div class="heatmap-wrap"><table class="heatmap" id="heatHits"></table></div>
    </div>
  </div>
</div>

<script id="report-data" type="application/json">{report_json}</script>
<script>
const report = JSON.parse(document.getElementById("report-data").textContent);
const COMPANY_ORDER = report.company_order;
const YEARS = report.years;
const FILING_BY_KEY = {{}};
report.filings.forEach(f => {{ FILING_BY_KEY[`${{f.ticker}}_${{f.year}}`] = f; }});

function renderHeatmap(elementId, valueFn, maxPct){{
  const el = document.getElementById(elementId);
  let head = "<thead><tr><th></th>" + COMPANY_ORDER.map(t => `<th>${{t}}</th>`).join("") + "</tr></thead>";
  let body = "<tbody>" + YEARS.map(year => {{
    const cells = COMPANY_ORDER.map(ticker => {{
      const d = FILING_BY_KEY[`${{ticker}}_${{year}}`];
      if (!d || d.total === 0) return `<td><div class="heat-cell heat-empty">&middot;</div></td>`;
      const pct = valueFn(d);
      const alpha = Math.min(100, Math.round((pct / maxPct) * 100));
      const bg = `color-mix(in srgb, var(--kept) ${{alpha}}%, var(--panel))`;
      const textColor = alpha > 55 ? "#fff" : "var(--ink)";
      return `<td><div class="heat-cell" style="background:${{bg}}; color:${{textColor}}">${{Math.round(pct)}}%</div></td>`;
    }}).join("");
    return `<tr><th class="mono">${{year}}</th>${{cells}}</tr>`;
  }}).join("") + "</tbody>";
  el.innerHTML = head + body;
}}

renderHeatmap("heatClassified", d => (d.classified / d.total) * 100, 85);
renderHeatmap("heatHits", d => (d.hits / d.total) * 100, 35);
</script>

</body>
</html>
"""


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "2_Adaptive_Rag_Classification.html"

    print("querying database...")
    report = build_report()

    print("rendering HTML...")
    html = HTML_TEMPLATE.format(report_json=json.dumps(report))
    output_path.write_text(html)

    print(f"wrote {output_path} ({len(html):,} bytes, {len(report['filings'])} filing-years)")


if __name__ == "__main__":
    main()
