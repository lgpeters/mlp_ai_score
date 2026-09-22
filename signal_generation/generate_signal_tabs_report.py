"""Static HTML report: the 3 raw signals (materiality, salience, sentiment),
tabbed by version -- ai_associated >= 1 (All), == 1 (General AI), == 2 (AI Infra).

Reuses build_signals.py's tested pipeline (load_raw/masked_copy/build_version)
so the numbers are guaranteed consistent with signals_multiindex.csv.

Usage: python3 generate_signal_tabs_report.py [output_html]
Default output: signal_tabs.html (this directory).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from build_signals import build_version, load_raw, masked_copy

REPO_DIR = Path(__file__).resolve().parent

TICKERS = ["ADBE", "CAT", "DELL", "INTC", "MU", "NVDA", "PG", "TEAM", "WM"]
# Fixed categorical order (dataviz skill palette, slots 1-8) + one extra
# distinguishable hue for the 9th series. Same ticker->color mapping is
# reused across every tab so a series never gets repainted when you switch.
TICKER_COLORS = {
    "ADBE": "#2a78d6", "CAT": "#eb6834", "DELL": "#1baf7a", "INTC": "#eda100",
    "MU": "#e87ba4", "NVDA": "#008300", "PG": "#4a3aa7", "TEAM": "#e34948",
    "WM": "#8a5a2f",
}

VERSIONS = {
    "ALL": {"label": "ai_associated ≥ 1 (All)", "mask_fn": lambda df: df["ai_associated"] >= 1},
    "ASSOC_1": {"label": "ai_associated == 1 (General AI)", "mask_fn": lambda df: df["ai_associated"] == 1},
    "ASSOC_2": {"label": "ai_associated == 2 (AI Infra)", "mask_fn": lambda df: df["ai_associated"] == 2},
}

FEATURE_LABELS = {
    "raw_materiality": "Materiality",
    "raw_salience": "Salience",
    "raw_sentiment": "Sentiment (Materiality × Salience)",
}


def build_report() -> dict:
    df_raw = load_raw()

    versions_out = {}
    for version_key, spec in VERSIONS.items():
        mask = spec["mask_fn"](df_raw)
        matrices = build_version(masked_copy(df_raw, mask))

        features_out = {}
        for feature, matrix in matrices.items():
            m = matrix[matrix.index >= "2015-01-01"]
            series = {
                ticker: {
                    "x": m.index.strftime("%Y-%m-%d").tolist(),
                    "y": [None if pd_isna(v) else round(float(v), 4) for v in m[ticker]],
                }
                for ticker in TICKERS if ticker in m.columns
            }
            features_out[feature] = series
        versions_out[version_key] = {"label": spec["label"], "features": features_out}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker_colors": TICKER_COLORS,
        "feature_labels": FEATURE_LABELS,
        "versions": versions_out,
    }


def pd_isna(v) -> bool:
    try:
        return v != v  # NaN != NaN
    except Exception:
        return v is None


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Signal Explorer</title>
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
  .subtitle {{ color: var(--muted); font-size: 0.95rem; margin-bottom: 1.5rem; font-family: -apple-system, sans-serif; }}

  .tabbar {{ display: flex; gap: 0.4rem; border-bottom: 2px solid var(--border); margin-bottom: 1.5rem; flex-wrap: wrap; }}
  .tabbar button {{
    font-family: -apple-system, sans-serif; font-size: 0.9rem; padding: 0.55rem 1rem; border: none; background: none;
    color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent; margin-bottom: -2px;
  }}
  .tabbar button.active {{ color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }}
  .tabbar button:hover {{ color: var(--text); }}

  .panel {{ display: none; }}
  .panel.active {{ display: block; }}

  .chart-card {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 1rem 1.2rem 0.5rem; margin-bottom: 1.2rem;
  }}
  .chart-card h2 {{ font-size: 0.95rem; margin: 0 0 0.3rem; color: var(--text); font-family: -apple-system, sans-serif; font-weight: 600; }}
  .chart-div {{ width: 100%; height: 340px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI Signal Explorer</h1>
  <div class="subtitle">Generated <span id="generated-at"></span>. 365-day rolling raw signals per ticker, from 2015 onward.</div>

  <div class="tabbar" id="tabbar"></div>
  <div id="panels"></div>
</div>

<script id="report-data" type="application/json">{report_json}</script>
<script>
const report = JSON.parse(document.getElementById("report-data").textContent);
document.getElementById("generated-at").textContent = new Date(report.generated_at).toLocaleString();

function plotFeature(divId, versionKey, feature) {{
  const seriesByTicker = report.versions[versionKey].features[feature];
  const traces = Object.entries(seriesByTicker).map(([ticker, s]) => ({{
    x: s.x, y: s.y, name: ticker, mode: "lines",
    line: {{ color: report.ticker_colors[ticker], width: 2 }},
    hovertemplate: `<b>${{ticker}}</b><br>%{{x}}<br>%{{y:.4f}}<extra></extra>`,
  }}));

  const fg = "#52514e";
  const grid = "#e2e0da";
  const bg = "#ffffff";

  Plotly.newPlot(divId, traces, {{
    margin: {{ l: 48, r: 16, t: 10, b: 36 }},
    paper_bgcolor: bg, plot_bgcolor: bg,
    font: {{ color: fg, size: 11, family: "-apple-system, sans-serif" }},
    xaxis: {{ gridcolor: grid, zeroline: false }},
    yaxis: {{ gridcolor: grid, zeroline: true, zerolinecolor: grid }},
    legend: {{ orientation: "h", y: -0.25 }},
    hovermode: "closest",
  }}, {{ responsive: true, displayModeBar: false }});
}}

const tabbar = document.getElementById("tabbar");
const panels = document.getElementById("panels");
const versionKeys = Object.keys(report.versions);

versionKeys.forEach((vKey, i) => {{
  const btn = document.createElement("button");
  btn.textContent = report.versions[vKey].label;
  btn.className = i === 0 ? "active" : "";
  btn.dataset.v = vKey;
  tabbar.appendChild(btn);

  const panel = document.createElement("div");
  panel.className = "panel" + (i === 0 ? " active" : "");
  panel.dataset.v = vKey;
  panel.innerHTML = Object.keys(report.feature_labels).map(feature => `
    <div class="chart-card">
      <h2>${{report.feature_labels[feature]}}</h2>
      <div class="chart-div" id="chart-${{vKey}}-${{feature}}"></div>
    </div>
  `).join("");
  panels.appendChild(panel);
}});

function renderTab(vKey) {{
  Object.keys(report.feature_labels).forEach(feature => {{
    plotFeature(`chart-${{vKey}}-${{feature}}`, vKey, feature);
  }});
}}

tabbar.addEventListener("click", (e) => {{
  const btn = e.target.closest("button");
  if (!btn) return;
  tabbar.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === btn));
  panels.querySelectorAll(".panel").forEach(p => {{
    const active = p.dataset.v === btn.dataset.v;
    p.classList.toggle("active", active);
    if (active) renderTab(btn.dataset.v);
  }});
}});

renderTab(versionKeys[0]);
</script>
</body>
</html>
"""


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_DIR / "signal_tabs.html"

    print("building signals...")
    report = build_report()

    print("rendering HTML...")
    html = HTML_TEMPLATE.format(report_json=json.dumps(report))
    output_path.write_text(html)

    print(f"wrote {output_path} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
