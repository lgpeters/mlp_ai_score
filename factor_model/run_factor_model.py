"""Per-company factor model isolating each stock's AI beta.

For each of the 9 tickers, runs:

    stock_excess_return ~ Mkt-RF + SMB + HML + RMW + CMA + Mom + Sector_excess + AI_factor

Two independent AI factor constructions, compared side by side:
  - AI_BASKET: equal-weight excess return of an ex-universe pure-play AI/semi
    basket (AVGO, TSM, AMD, SMCI, ASML) -- sourced from ARTY's own real
    holdings (see ai_factor_ingredients.csv), not hand-picked from hindsight.
  - AI_ARTY: excess return of the iShares Future AI & Tech ETF (formerly IRBO,
    renamed 2023 -- earlier "delisted" conclusion in this project was wrong,
    it was a ticker rename).

Both AI factors are orthogonalized against Mkt-RF and the company's own
sector ETF excess return (Frisch-Waugh-Lovell: regress the AI factor on
[const, Mkt-RF, Sector], take the residual) BEFORE being used as a
regressor, per the brief's explicit requirement. The resulting AI beta is
therefore the AI factor's marginal contribution net of market and sector,
not a repackaged tech/market bet.

Known, documented limitations (see LIMITATIONS.md):
  - Basket composition (AVGO/TSM/AMD/SMCI/ASML) reflects today's known AI
    winners -- a look-ahead/selection-bias risk in what counts as
    "pure-play AI" this far back (AVGO's own history only starts 2009).
  - Both AI factors are holdings-based/price-based, deliberately NOT
    text-derived, specifically so this model is independent of (not
    circular with) the second-pass classification signal built earlier in
    this project. It is not used to validate or be validated by that signal.
  - This is a contemporaneous (same-day) factor regression, the standard
    Fama-French style setup -- not a predictive/forward-return regression,
    so there is no forward-looking-return leakage. The only real look-ahead
    risk is the basket-selection one above.
  - NVDA (and, for the ARTY construction, MU) are real, meaningful holdings
    within ARTY itself (~4.9% and ~4.6% respectively) -- their AI_ARTY beta
    should be read with that mechanical overlap in mind. Neither is a
    component of the ex-universe basket, so AI_BASKET has no such overlap
    for any of the 9 tickers by construction.

Usage: python3 factor_model/run_factor_model.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

REPO_ROOT = Path(__file__).resolve().parent.parent
FACTOR_DIR = Path(__file__).resolve().parent

SECTOR_ETF = {
    "ADBE": "IGV", "TEAM": "IGV",           # software
    "NVDA": "SOXX", "INTC": "SOXX", "MU": "SOXX",  # semiconductors
    "DELL": "XLK",                           # hardware
    "CAT": "XLI", "WM": "XLI",               # industrials
    "PG": "XLP",                             # consumer staples
}
BASKET_TICKERS = ["AVGO", "TSM", "AMD", "SMCI", "ASML"]
CONTROL_COLS = ["mkt_rf", "smb_5", "hml_5", "rmw", "cma", "mom"]


def load_returns() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stocks = pd.read_csv(FACTOR_DIR / "stocks.csv", parse_dates=["date"])
    index = pd.read_csv(FACTOR_DIR / "index.csv", parse_dates=["date"])
    factors = pd.read_csv(FACTOR_DIR / "factors.csv", parse_dates=["date"])
    ingredients = pd.read_csv(FACTOR_DIR / "ai_factor_ingredients.csv", parse_dates=["date"])

    def to_returns(df: pd.DataFrame) -> pd.DataFrame:
        df = df.sort_values(["ticker", "date"]).copy()
        df["ret"] = df.groupby("ticker")["closeprice"].pct_change()
        return df

    stock_returns = to_returns(stocks).pivot(index="date", columns="ticker", values="ret")
    ingredient_returns = to_returns(ingredients).pivot(index="date", columns="ticker", values="ret")

    factors = factors.set_index("date")
    factors[["mkt_rf", "rf", "smb_3", "hml_3", "smb_5", "hml_5", "rmw", "cma", "mom"]] /= 100.0  # French data is in percent

    return stock_returns, ingredient_returns, factors, index


def build_ai_factors(ingredient_returns: pd.DataFrame, mkt_rf: pd.Series) -> pd.DataFrame:
    basket = ingredient_returns[BASKET_TICKERS].mean(axis=1, skipna=False)
    arty = ingredient_returns["ARTY"]
    out = pd.DataFrame({
        "AI_BASKET_excess": basket - mkt_rf,
        "AI_ARTY_excess": arty - mkt_rf,
    })
    return out


def orthogonalize(ai_factor: pd.Series, mkt_rf: pd.Series, sector_excess: pd.Series) -> pd.Series:
    """FWL residual: AI factor with market + sector effects partialled out."""
    df = pd.DataFrame({"ai": ai_factor, "mkt_rf": mkt_rf, "sector": sector_excess}).dropna()
    X = sm.add_constant(df[["mkt_rf", "sector"]])
    resid = sm.OLS(df["ai"], X).fit().resid
    return resid.reindex(ai_factor.index)


def run_one(ticker: str, stock_ret: pd.Series, factors: pd.DataFrame,
            ingredient_returns: pd.DataFrame, ai_factors: pd.DataFrame, ai_factor_name: str) -> dict:
    sector_etf = SECTOR_ETF[ticker]
    sector_excess = ingredient_returns[sector_etf] - factors["mkt_rf"]
    ai_raw = ai_factors[ai_factor_name]
    ai_orth = orthogonalize(ai_raw, factors["mkt_rf"], sector_excess)

    stock_excess = stock_ret - factors["rf"]

    df = pd.concat([stock_excess.rename("y"), factors[CONTROL_COLS], sector_excess.rename("sector_excess"),
                     ai_orth.rename("ai_orth")], axis=1).dropna()

    X = sm.add_constant(df[CONTROL_COLS + ["sector_excess", "ai_orth"]])
    model = sm.OLS(df["y"], X).fit()

    return {
        "ticker": ticker,
        "sector_etf": sector_etf,
        "ai_factor": ai_factor_name,
        "n_obs": int(model.nobs),
        "date_range": f"{df.index.min().date()} -> {df.index.max().date()}",
        "ai_beta": model.params["ai_orth"],
        "ai_t": model.tvalues["ai_orth"],
        "ai_p": model.pvalues["ai_orth"],
        "mkt_beta": model.params["mkt_rf"],
        "sector_beta": model.params["sector_excess"],
        "r_squared": model.rsquared,
        "corr_ai_mkt": df["ai_orth"].corr(df["mkt_rf"]),
        "corr_ai_sector": df["ai_orth"].corr(df["sector_excess"]),
        "corr_ai_mom": df["ai_orth"].corr(df["mom"]),
    }


def main() -> None:
    print("loading data...")
    stock_returns, ingredient_returns, factors, _index = load_returns()
    ai_factors = build_ai_factors(ingredient_returns, factors["mkt_rf"])

    print(f"AI_BASKET usable from: {ai_factors['AI_BASKET_excess'].dropna().index.min().date()} "
          f"(bounded by AVGO's 2009 IPO)")
    print(f"AI_ARTY usable from: {ai_factors['AI_ARTY_excess'].dropna().index.min().date()} (ETF launch 2018)")
    print()

    results = []
    for ticker in SECTOR_ETF:
        for ai_factor_name in ["AI_BASKET_excess", "AI_ARTY_excess"]:
            results.append(run_one(ticker, stock_returns[ticker], factors, ingredient_returns, ai_factors, ai_factor_name))

    results_df = pd.DataFrame(results)
    results_df.to_csv(FACTOR_DIR / "ai_betas.csv", index=False)

    print("=== AI beta by ticker, both constructions (orthogonalized vs. market + sector) ===")
    pivot = results_df.pivot(index="ticker", columns="ai_factor", values=["ai_beta", "ai_t", "n_obs"])
    print(pivot.round(3).to_string())
    print()

    print("=== Multicollinearity check: correlation of orthogonalized AI factor with market/sector/momentum ===")
    print(results_df[["ticker", "ai_factor", "corr_ai_mkt", "corr_ai_sector", "corr_ai_mom"]].round(3).to_string(index=False))
    print()

    print(f"wrote {FACTOR_DIR / 'ai_betas.csv'} ({len(results_df)} rows)")


if __name__ == "__main__":
    main()
