"""Static HTML report showing the signal_analysis.ipynb walkthrough:

  1. Raw Materiality (Entire Corpus) -- ALL version, all 9 tickers
  2. Raw Sentiment (Entire Corpus) -- ALL version, all 9 tickers
  3. Raw Sentiment (AI Product - Mentions) -- ASSOC1, all 9 tickers
  4. Raw Sentiment (AI Infra - Mentions) -- ASSOC2, all 9 tickers
  5. NVDA -- Sentiment by Category (Boilerplate Risk, AI Partnership,
     Operational Efficiencies, Restructuring, Product Roadmap, Financial Exposure)
  6. ADBE -- same, by Category

Reuses quant/build_signals.py's tested pipeline (load_raw/masked_copy/
build_version/parse_category_labels) so the numbers are guaranteed
consistent with signals_multiindex.csv -- no reimplementation.

Usage: python3 generate_signal_explorer_report.py [output_html]
Default output: 5_Signal_Explorer.html (repo root).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "quant"))

from build_signals import build_version, load_raw, masked_copy, parse_category_labels

DATE_FLOOR = "2015-01-01"
BASELINE_START = "2017-01-01"
BASELINE_END = "2022-12-31"
TICKERS = ["ADBE", "CAT", "DELL", "INTC", "MU", "NVDA", "PG", "TEAM", "WM"]

TICKER_COLORS = {
    "ADBE": "#2a78d6", "CAT": "#eb6834", "DELL": "#1baf7a", "INTC": "#eda100",
    "MU": "#e87ba4", "NVDA": "#008300", "PG": "#4a3aa7", "TEAM": "#e34948",
    "WM": "#8a5a2f",
}
CATEGORY_COLORS = {
    "Boilerplate Risk": "#83817a", "AI Partnership": "#2a78d6",
    "Operational Efficiencies": "#1baf7a", "Restructuring": "#eda100",
    "Product Roadmap": "#4a3aa7", "Financial Exposure": "#e34948",
}
CATEGORY_ORDER = ["Boilerplate Risk", "AI Partnership", "Operational Efficiencies",
                   "Restructuring", "Product Roadmap", "Financial Exposure"]


def series_from_matrix(matrix) -> dict:
    m = matrix[matrix.index >= DATE_FLOOR]
    return {
        ticker: {
            "x": m.index.strftime("%Y-%m-%d").tolist(),
            "y": [None if v != v else round(float(v), 4) for v in m[ticker]],
        }
        for ticker in TICKERS if ticker in m.columns
    }


def build_report() -> dict:
    df_raw = load_raw()

    category_labels = parse_category_labels()
    merged_groups = {4, 5}

    versions: dict[str, "pd.Series"] = {
        "ALL": (df_raw["ai_associated"] >= 1) & ~df_raw["is_boilerplate_risk"],
        "ASSOC1": df_raw["ai_associated"] == 1,
        "ASSOC2": df_raw["ai_associated"] == 2,
        "Boilerplate Risk": df_raw["is_boilerplate_risk"],
    }
    for g, label in category_labels.items():
        if g in merged_groups:
            continue
        versions[label] = df_raw["ai_group"] == g
    versions["Financial Exposure"] = df_raw["ai_group"].isin(merged_groups) & ~df_raw["is_boilerplate_risk"]

    computed = {}
    for name, mask in versions.items():
        print(f"building version: {name} ({int(mask.sum())} matching chunks)")
        computed[name] = build_version(masked_copy(df_raw, mask))

    corpus = {
        "raw_materiality": series_from_matrix(computed["ALL"]["raw_materiality"]),
        "raw_sentiment": series_from_matrix(computed["ALL"]["raw_sentiment"]),
    }

    # AI Sentiment Score: raw_sentiment (ALL version) z-scored against a FIXED
    # baseline distribution (2017-2022, pooled across all 9 tickers) rather than
    # a rolling/contemporaneous cross-section -- so a ticker's score keeps
    # climbing as it genuinely diverges from pre-boom normal, instead of
    # mechanically compressing as the rest of the peer group catches up.
    raw_sentiment_matrix = computed["ALL"]["raw_sentiment"]
    baseline_period = raw_sentiment_matrix.loc[BASELINE_START:BASELINE_END]
    baseline_values = baseline_period.values.flatten()
    baseline_values = baseline_values[~np.isnan(baseline_values)]
    baseline_mean = float(baseline_values.mean())
    baseline_std = float(baseline_values.std())
    z_sentiment_matrix = (raw_sentiment_matrix - baseline_mean) / baseline_std

    ai_sentiment_score = series_from_matrix(z_sentiment_matrix)

    latest_date = z_sentiment_matrix.dropna(how="all").index.max()
    latest_row = z_sentiment_matrix.loc[latest_date]
    latest_cross_section = {
        "date": latest_date.strftime("%Y-%m-%d"),
        "values": {t: (None if v != v else round(float(v), 4)) for t, v in latest_row.items() if t in TICKERS},
    }
    assoc1_sentiment = series_from_matrix(computed["ASSOC1"]["raw_sentiment"])
    assoc2_sentiment = series_from_matrix(computed["ASSOC2"]["raw_sentiment"])

    category_sentiment = {}
    for ticker in ["NVDA", "ADBE"]:
        category_sentiment[ticker] = {}
        for cat in CATEGORY_ORDER:
            m = computed[cat]["raw_sentiment"]
            m = m[m.index >= DATE_FLOOR]
            if ticker not in m.columns:
                continue
            category_sentiment[ticker][cat] = {
                "x": m.index.strftime("%Y-%m-%d").tolist(),
                "y": [None if v != v else round(float(v), 4) for v in m[ticker]],
            }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker_colors": TICKER_COLORS,
        "category_colors": CATEGORY_COLORS,
        "category_order": CATEGORY_ORDER,
        "ai_sentiment_score": ai_sentiment_score,
        "ai_sentiment_baseline": {"start": BASELINE_START, "end": BASELINE_END, "mean": round(baseline_mean, 4), "std": round(baseline_std, 4)},
        "latest_cross_section": latest_cross_section,
        "corpus": corpus,
        "assoc1_sentiment": assoc1_sentiment,
        "assoc2_sentiment": assoc2_sentiment,
        "category_sentiment": category_sentiment,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Signal Explorer</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.0/plotly.min.js"></script>
<style>
  :root {{
    --bg: #f7f7f5; --panel: #ffffff; --border: #e2e0da; --text: #24231f;
    --muted: #6b6a64; --accent: #a8560f; --accent-soft: #f4e6d8;
    --code-bg: #f1efe9;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2.5rem 1.5rem 4rem; background: var(--bg); color: var(--text);
    font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif; line-height: 1.55;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.9rem; margin: 0 0 0.25rem; letter-spacing: -0.01em; }}
  .subtitle {{ color: var(--muted); font-size: 0.95rem; margin-bottom: 0.6rem; font-family: -apple-system, sans-serif; }}
  .methodology {{
    font-family: -apple-system, sans-serif; font-size: 0.85rem; color: var(--text);
    background: var(--code-bg); border-left: 3px solid var(--accent); border-radius: 4px;
    padding: 0.6rem 0.85rem; margin-bottom: 1.8rem;
  }}
  h2.section-title {{
    font-family: -apple-system, sans-serif; font-size: 0.78rem; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--accent); margin: 2.2rem 0 0.8rem;
  }}

  .chart-card {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 1rem 1.2rem 0.5rem; margin-bottom: 1.2rem;
  }}
  .chart-card h3 {{ font-size: 0.95rem; margin: 0 0 0.3rem; color: var(--text); font-family: -apple-system, sans-serif; font-weight: 600; }}
  .chart-div {{ width: 100%; height: 340px; }}
  .pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }}
  @media (max-width: 800px) {{ .pair {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Signal Explorer</h1>
  <div class="subtitle">365-day rolling raw signals, from 2015 onward.</div>
  <div class="methodology">Materiality (1-10) &times; Salience (-3 to +3) = Sentiment. Each score is summed over the trailing 365 days and divided by the total chunks considered in that window, so it reflects both how material the AI content was and how much of it there was.</div>

  <h2 class="section-title">AI Sentiment Score</h2>
  <div class="methodology" id="sentiment-score-note"></div>
  <div class="chart-card"><h3>AI Sentiment Score (z-scored vs. 2017-2022 baseline)</h3><div class="chart-div" id="chart-sentiment-score"></div></div>
  <div class="chart-card"><h3 id="cross-section-title">Latest Cross-Section</h3><div class="chart-div" id="chart-cross-section"></div></div>

  <h2 class="section-title">Entire corpus</h2>
  <div class="chart-card"><h3>Raw Materiality (Entire Corpus)</h3><div class="chart-div" id="chart-corpus-mat"></div></div>
  <div class="chart-card"><h3>Raw Sentiment (Entire Corpus)</h3><div class="chart-div" id="chart-corpus-sent"></div></div>

  <h2 class="section-title">Stage 1 split -- Product vs. Infra mentions</h2>
  <div class="chart-card"><h3>Raw Sentiment (AI Product &mdash; Mentions)</h3><div class="chart-div" id="chart-assoc1"></div></div>
  <div class="chart-card"><h3>Raw Sentiment (AI Infra &mdash; Mentions)</h3><div class="chart-div" id="chart-assoc2"></div></div>

  <h2 class="section-title">Stage 2 split -- Sentiment by category</h2>
  <div class="chart-card"><h3>NVIDIA &mdash; Sentiment by Category</h3><div class="chart-div" id="chart-cat-nvda"></div></div>
  <div class="chart-card"><h3>ADBE &mdash; Sentiment by Category</h3><div class="chart-div" id="chart-cat-adbe"></div></div>
</div>

<script id="report-data" type="application/json">{report_json}</script>
<script>
const report = JSON.parse(document.getElementById("report-data").textContent);

function layoutFor() {{
  const fg = "#52514e";
  const grid = "#e2e0da";
  const bg = "#ffffff";
  return {{
    margin: {{ l: 48, r: 16, t: 10, b: 36 }},
    paper_bgcolor: bg, plot_bgcolor: bg,
    font: {{ color: fg, size: 11, family: "-apple-system, sans-serif" }},
    xaxis: {{ gridcolor: grid, zeroline: false }},
    yaxis: {{ gridcolor: grid, zeroline: true, zerolinecolor: grid }},
    legend: {{ orientation: "h", y: -0.25 }},
    hovermode: "closest",
  }};
}}

function plotByTicker(divId, seriesByTicker) {{
  const traces = Object.entries(seriesByTicker).map(([ticker, s]) => ({{
    x: s.x, y: s.y, name: ticker, mode: "lines",
    line: {{ color: report.ticker_colors[ticker], width: 2 }},
    hovertemplate: `<b>${{ticker}}</b><br>%{{x}}<br>%{{y:.4f}}<extra></extra>`,
  }}));
  Plotly.newPlot(divId, traces, layoutFor(), {{ responsive: true, displayModeBar: false }});
}}

function plotByCategory(divId, seriesByCategory) {{
  const traces = report.category_order
    .filter(cat => cat in seriesByCategory)
    .map(cat => {{
      const s = seriesByCategory[cat];
      return {{
        x: s.x, y: s.y, name: cat, mode: "lines",
        line: {{ color: report.category_colors[cat], width: 2 }},
        hovertemplate: `<b>${{cat}}</b><br>%{{x}}<br>%{{y:.4f}}<extra></extra>`,
      }};
    }});
  Plotly.newPlot(divId, traces, layoutFor(), {{ responsive: true, displayModeBar: false }});
}}

function plotCrossSection(divId, latestCrossSection) {{
  const entries = Object.entries(latestCrossSection.values)
    .filter(([, v]) => v !== null)
    .sort((a, b) => b[1] - a[1]);
  const tickers = entries.map(([t]) => t);
  const values = entries.map(([, v]) => v);
  const colors = tickers.map(t => report.ticker_colors[t]);

  const trace = {{
    x: values, y: tickers, type: "bar", orientation: "h",
    marker: {{ color: colors }},
    hovertemplate: `<b>%{{y}}</b><br>%{{x:.3f}}<extra></extra>`,
  }};
  const layout = layoutFor();
  layout.yaxis = {{ ...layout.yaxis, autorange: "reversed" }};
  layout.margin = {{ l: 56, r: 24, t: 10, b: 36 }};
  delete layout.legend;
  Plotly.newPlot(divId, [trace], layout, {{ responsive: true, displayModeBar: false }});
}}

document.getElementById("sentiment-score-note").textContent =
  `Z-scored against a fixed baseline distribution (${{report.ai_sentiment_baseline.start}} to ${{report.ai_sentiment_baseline.end}}, pooled across all 9 tickers: mean=${{report.ai_sentiment_baseline.mean}}, std=${{report.ai_sentiment_baseline.std}}) rather than a rolling per-date cross-section, so a ticker's score keeps climbing as it genuinely diverges from pre-boom normal instead of compressing as peers catch up.`;
document.getElementById("cross-section-title").textContent = `Latest Cross-Section (as of ${{report.latest_cross_section.date}})`;

plotByTicker("chart-sentiment-score", report.ai_sentiment_score);
plotCrossSection("chart-cross-section", report.latest_cross_section);
plotByTicker("chart-corpus-mat", report.corpus.raw_materiality);
plotByTicker("chart-corpus-sent", report.corpus.raw_sentiment);
plotByTicker("chart-assoc1", report.assoc1_sentiment);
plotByTicker("chart-assoc2", report.assoc2_sentiment);
plotByCategory("chart-cat-nvda", report.category_sentiment.NVDA);
plotByCategory("chart-cat-adbe", report.category_sentiment.ADBE);
</script>
</body>
</html>
"""


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "5_Signal_Explorer.html"

    print("querying database...")
    report = build_report()

    print("rendering HTML...")
    html = HTML_TEMPLATE.format(report_json=json.dumps(report))
    output_path.write_text(html)

    print(f"wrote {output_path} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
