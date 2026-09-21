"""Experiment: compare rolling-window lengths (30/60/90/180d) for the
combined (all-content-type) materiality/saliency/intensity signal, per
company, and plot them. Saved as PNGs for a quick visual scan of which
window is smooth-but-responsive vs. too noisy/too dead.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import matplotlib.pyplot as plt

from quantexperiment.data_loader import load_first_pass, load_second_pass
from quantexperiment.signals import build_full_grid, daily_panel, rolling_signal

OUT_DIR = Path(__file__).resolve().parent / "out"
OUT_DIR.mkdir(exist_ok=True)

TICKERS = ["ADBE", "CAT", "DELL", "INTC", "MU", "NVDA", "PG", "TEAM", "WM"]
WINDOWS = [30, 60, 90, 180]


def main():
    sp = load_second_pass()
    fp = load_first_pass()
    panel = daily_panel(sp, fp, content_type=None)
    grid = build_full_grid(panel, TICKERS)

    fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True)
    for w in WINDOWS:
        rolled = rolling_signal(grid, window_days=w, min_chunks=3)
        # focus on NVDA (highest volume) for a clean visual comparison
        nvda = rolled[rolled["ticker"] == "NVDA"].sort_values("published_date")
        nvda = nvda[nvda["published_date"] >= "2018-01-01"]
        axes[0].plot(nvda["published_date"], nvda["materiality"], label=f"{w}d")
        axes[1].plot(nvda["published_date"], nvda["saliency"], label=f"{w}d")
        axes[2].plot(nvda["published_date"], nvda["intensity"], label=f"{w}d")

    axes[0].set_title("NVDA -- combined materiality by rolling window")
    axes[1].set_title("NVDA -- combined saliency by rolling window")
    axes[2].set_title("NVDA -- combined intensity (% chunks AI-relevant) by rolling window")
    for ax in axes:
        ax.legend(fontsize=8)
        ax.axhline(0, color="gray", linewidth=0.5)
    plt.tight_layout()
    out_path = OUT_DIR / "exp_windows_nvda.png"
    plt.savefig(out_path, dpi=110)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
