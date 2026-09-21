"""Turns the raw per-chunk signal into a single daily, cross-sectionally
comparable time series per company (and per content type).

Design:
  1. Build a daily panel (every ticker x every day in range x content_type),
     resampling raw per-chunk events onto a daily grid.
  2. Rolling window (configurable, default 60d) mean materiality and mean
     saliency -- both simple and materiality-weighted saliency (weighting
     each chunk's saliency by its own materiality, so a highly material
     but mildly negative chunk counts for more than a trivial mention).
  3. Intensity = rolling (# second-pass / # first-pass-considered) in the
     same window -- "how much of what we scanned was actually AI-relevant"
     -- the volume/attention-adjusted piece the raw score alone misses.
  4. Cross-sectional step: at every date, z-score each company's rolling
     materiality/saliency/intensity against the other 8 companies THAT
     DAY. This is what makes the series "cross-sectionally relevant" --
     comparable across companies at a point in time, not just each
     company's own history.
"""

import numpy as np
import pandas as pd


def daily_panel(second_pass: pd.DataFrame, first_pass: pd.DataFrame, content_type: str | None = None) -> pd.DataFrame:
    """Collapse raw chunk rows to one row per (ticker, date, content_type)
    with counts + sums needed for rolling aggregation. content_type=None
    combines all sources."""
    sp = second_pass if content_type is None else second_pass[second_pass["content_type"] == content_type]
    fp = first_pass if content_type is None else first_pass[first_pass["content_type"] == content_type]

    sp_daily = sp.groupby(["ticker", "published_date"]).agg(
        n_second=("ai_materiality", "size"),
        materiality_sum=("ai_materiality", "sum"),
        saliency_sum=("ai_saliency", "sum"),
        weighted_saliency_sum=("ai_materiality", lambda s: (s * sp.loc[s.index, "ai_saliency"]).sum()),
    ).reset_index()

    fp_daily = fp.groupby(["ticker", "published_date"]).agg(n_first=("ai_relevant", "size")).reset_index()

    panel = pd.merge(fp_daily, sp_daily, on=["ticker", "published_date"], how="left").fillna(0)
    return panel


def build_full_grid(panel: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Reindex onto a dense daily grid per ticker so rolling windows see
    real gaps (no data that day) rather than skipping straight to the
    next event."""
    if panel.empty:
        return panel
    full_range = pd.date_range(panel["published_date"].min(), panel["published_date"].max(), freq="D")
    grids = []
    for t in tickers:
        g = panel[panel["ticker"] == t].set_index("published_date").reindex(full_range).fillna(0)
        g["ticker"] = t
        g.index.name = "published_date"
        grids.append(g.reset_index())
    return pd.concat(grids, ignore_index=True)


def rolling_signal(panel: pd.DataFrame, window_days: int = 60, min_chunks: int = 3) -> pd.DataFrame:
    """Per-ticker rolling sums over window_days, then derive:
      - materiality: rolling mean ai_materiality among second-pass chunks
      - saliency: rolling mean ai_saliency (simple)
      - weighted_saliency: rolling materiality-weighted mean ai_saliency
      - intensity: rolling n_second / n_first (share of scanned content that's AI-relevant)
    A window with fewer than min_chunks second-pass hits gets NaN materiality/saliency
    (too little signal to trust an average), but intensity is always computed
    since it's meaningful even at 0.
    """
    out = []
    for ticker, g in panel.groupby("ticker"):
        g = g.sort_values("published_date").copy()
        roll = g[["n_second", "n_first", "materiality_sum", "saliency_sum", "weighted_saliency_sum"]].rolling(
            window=window_days, min_periods=1
        ).sum()

        g["roll_n_second"] = roll["n_second"]
        g["roll_n_first"] = roll["n_first"]
        g["materiality"] = np.where(roll["n_second"] >= min_chunks, roll["materiality_sum"] / roll["n_second"], np.nan)
        g["saliency"] = np.where(roll["n_second"] >= min_chunks, roll["saliency_sum"] / roll["n_second"], np.nan)
        g["weighted_saliency"] = np.where(
            roll["n_second"] >= min_chunks, roll["weighted_saliency_sum"] / roll["materiality_sum"].replace(0, np.nan), np.nan
        )
        g["intensity"] = np.where(roll["n_first"] > 0, roll["n_second"] / roll["n_first"], 0.0)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def cross_sectional_zscore(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """z-score each column across tickers, per date -- what makes the
    series comparable cross-sectionally rather than just within one
    company's own history."""
    df = df.copy()
    for col in cols:
        grouped = df.groupby("published_date")[col]
        mean = grouped.transform("mean")
        std = grouped.transform("std").replace(0, np.nan)
        df[f"{col}_z"] = (df[col] - mean) / std
    return df
