import sys
from pathlib import Path

import pandas as pd
import statsmodels.api as sm

FACTOR_DIR = Path(__file__).resolve().parent


# =============================================================================
# STEP 1 -- Load the raw CSVs
# =============================================================================

stocks = pd.read_csv(FACTOR_DIR / "stocks.csv", parse_dates=["date"])
# columns: ticker, date, closeprice -- one row per (ticker, date), our 9 companies

ingredients = pd.read_csv(FACTOR_DIR / "ai_factor_ingredients.csv", parse_dates=["date"])
# columns: ticker, date, closeprice -- sector ETFs (IGV/SOXX/XLK/XLI/XLP), ARTY,
# and the pure-play basket names (AVGO/TSM/AMD/SMCI/ASML)

factors = pd.read_csv(FACTOR_DIR / "factors.csv", parse_dates=["date"])
# columns: date, mkt_rf, rf, smb_3, hml_3, smb_5, hml_5, rmw, cma, mom
# Kenneth French's daily data, values in PERCENT (e.g. 0.50 means 0.50%)

print(f"loaded: {len(stocks)} stock rows, {len(ingredients)} ingredient rows, {len(factors)} factor rows")


# =============================================================================
# STEP 2 -- Convert prices to daily returns (pandas: groupby + pct_change)
# =============================================================================

# .sort_values first so pct_change() walks forward in time within each ticker,
# not in whatever row order the CSV happened to be in.
stocks = stocks.sort_values(["ticker", "date"])
stocks["ret"] = stocks.groupby("ticker")["closeprice"].pct_change()

ingredients = ingredients.sort_values(["ticker", "date"])
ingredients["ret"] = ingredients.groupby("ticker")["closeprice"].pct_change()

# pandas .pivot(): long (ticker, date, ret) -> wide (date index, one column per ticker).
# This is the shape we need for aligning multiple return series on the same date axis.
stock_returns = stocks.pivot(index="date", columns="ticker", values="ret")
ingredient_returns = ingredients.pivot(index="date", columns="ticker", values="ret")

# Fama-French data is in percent -- divide by 100 so it's on the same decimal
# scale as our pct_change() returns above (e.g. 0.005 = 0.5%, not 0.5 = 50%).
factors = factors.set_index("date")
pct_cols = ["mkt_rf", "rf", "smb_3", "hml_3", "smb_5", "hml_5", "rmw", "cma", "mom"]
factors[pct_cols] = factors[pct_cols] / 100.0

print(f"stock_returns shape: {stock_returns.shape}, ingredient_returns shape: {ingredient_returns.shape}")


# =============================================================================
# STEP 3 -- Build the two AI factor candidates, as EXCESS returns (over Mkt-RF)
# =============================================================================

BASKET_TICKERS = [
    "AVGO", "TSM", "AMD", "SMCI", "ASML",           # AI hardware / foundry
    "GOOGL", "META", "AMZN", "CRM", "NOW",           # mega-cap AI software/platforms
]

# Equal-weight average return across the basket -- pandas .mean(axis=1) averages
# ACROSS COLUMNS (i.e. across tickers) for each date, giving one number per day.
# skipna=False is essential here: AMD alone has data back to 1980, so a plain
# .mean() would silently average whatever subset of the 10 names happens to
# exist on a given day (a shrinking, discontinuous "basket") instead of only
# ever averaging the same fixed 10 names, starting cleanly once all 10 exist.
basket_return = ingredient_returns[BASKET_TICKERS].mean(axis=1, skipna=False)

ai_basket_raw = basket_return - factors["mkt_rf"]          # excess-of-market return
ai_arty_raw = ingredient_returns["ARTY"] - factors["mkt_rf"]

print(f"AI_BASKET usable from {ai_basket_raw.dropna().index.min().date()} (bounded by the newest basket IPO -- META/NOW, both 2012)")
print(f"AI_ARTY usable from {ai_arty_raw.dropna().index.min().date()} (ETF launched 2018)")


# =============================================================================
# STEP 4 -- Per-company sector assignment (real GICS-consistent mapping)
# =============================================================================

# SOXX (semiconductors) and XLK (broad tech) were both tried and rejected as
# the sector control for NVDA/INTC/MU/DELL: real holdings data showed NVDA is
# SOXX's #1 holding (9.4%) AND XLK's #1 holding (14.4%), with MU/INTC/AMD/AVGO
# also present in both -- "controlling for sector" would have meant
# controlling for a benchmark substantially made of the very companies (and
# AI_BASKET ingredients) being tested. Replaced with a custom equal-weight
# "Tech (ex-universe)" basket -- AAPL, MSFT, CSCO, ORCL, IBM -- broad,
# well-known tech names, explicitly NOT semiconductor/AI-thematic, and
# explicitly excluding every one of our 9 universe tickers and every
# AI_BASKET ingredient.
TECH_EXUNIV_TICKERS = ["AAPL", "MSFT", "CSCO", "ORCL", "IBM"]

SECTOR_ETF = {
    "ADBE": "IGV", "TEAM": "IGV",                              # software
    "NVDA": "TECH_EXUNIV", "INTC": "TECH_EXUNIV", "MU": "TECH_EXUNIV",  # semiconductors
    "DELL": "TECH_EXUNIV",                                     # hardware
    "CAT": "XLI", "WM": "XLI",                                 # industrials
    "PG": "XLP",                                               # consumer staples
}

# Build the custom basket the same way AI_BASKET was built (equal-weight,
# skipna=False so it never silently averages a shrinking subset of names),
# then drop it into ingredient_returns under a synthetic "ticker" so the rest
# of the pipeline can look it up exactly like any real ETF column.
ingredient_returns["TECH_EXUNIV"] = ingredient_returns[TECH_EXUNIV_TICKERS].mean(axis=1, skipna=False)

CONTROL_COLS = ["mkt_rf", "smb_5", "hml_5", "rmw", "cma", "mom"]


# =============================================================================
# STEP 5 -- Orthogonalize each AI factor against the FULL control set + sector
# =============================================================================
# This is the Frisch-Waugh-Lovell trick: regress the AI factor on everything
# else it will later share a regression with, and keep only the RESIDUAL.
# That residual is, by construction, uncorrelated with every control -- so
# its coefficient in the main regression can't secretly be absorbing
# growth/value/quality/momentum exposure instead of genuine AI exposure.

def orthogonalize(ai_factor: pd.Series, sector_excess: pd.Series) -> pd.Series:
    df = pd.concat([ai_factor.rename("ai"), factors[CONTROL_COLS], sector_excess.rename("sector")], axis=1).dropna()
    X = sm.add_constant(df[CONTROL_COLS + ["sector"]])
    residual = sm.OLS(df["ai"], X).fit().resid
    # .reindex() puts the residual back on the ORIGINAL full date index (with
    # NaN wherever we didn't have enough data to compute it), so it still
    # lines up correctly when we concat it with other series later.
    return residual.reindex(ai_factor.index)


# =============================================================================
# STEP 6 -- Run one regression per (company, AI factor construction) pair
# =============================================================================

results = []

for ticker, sector_etf in SECTOR_ETF.items():
    sector_excess = ingredient_returns[sector_etf] - factors["mkt_rf"]
    company_excess_return = stock_returns[ticker] - factors["rf"]

    for ai_name, ai_raw in [("AI_BASKET", ai_basket_raw), ("AI_ARTY", ai_arty_raw)]:
        ai_orthogonal = orthogonalize(ai_raw, sector_excess)

        # pandas pd.concat(axis=1) aligns every series on the shared DATE index.
        # .dropna() then keeps only dates where we have EVERY piece we need --
        # the company's return, all six Fama-French controls, the sector
        # return, and the orthogonalized AI factor.
        reg_df = pd.concat(
            [
                company_excess_return.rename("y"),
                factors[CONTROL_COLS],
                sector_excess.rename("sector_excess"),
                ai_orthogonal.rename("ai_orthogonal"),
            ],
            axis=1,
        ).dropna()

        X = sm.add_constant(reg_df[CONTROL_COLS + ["sector_excess", "ai_orthogonal"]])
        model = sm.OLS(reg_df["y"], X).fit()

        # Sanity check that the orthogonalization actually worked: this should
        # be ~0.000 for every control, by construction.
        max_leftover_corr = reg_df[CONTROL_COLS + ["sector_excess"]].corrwith(reg_df["ai_orthogonal"]).abs().max()

        results.append({
            "ticker": ticker,
            "sector_etf": sector_etf,
            "ai_factor": ai_name,
            "n_obs": int(model.nobs),
            "ai_beta": round(model.params["ai_orthogonal"], 4),
            "ai_t_stat": round(model.tvalues["ai_orthogonal"], 3),
            "ai_p_value": round(model.pvalues["ai_orthogonal"], 4),
            "r_squared": round(model.rsquared, 4),
            "max_leftover_corr_with_controls": round(max_leftover_corr, 4),
        })

results_df = pd.DataFrame(results)


# =============================================================================
# STEP 7 -- Cross-sectional view: pivot so each company's two betas sit side by side
# =============================================================================

cross_section = results_df.pivot(index="ticker", columns="ai_factor", values=["ai_beta", "ai_t_stat", "n_obs"])
cross_section = cross_section.sort_values(("ai_beta", "AI_BASKET"), ascending=False)

print()
print("=== Cross-sectional AI betas, both constructions, sorted by AI_BASKET beta ===")
print(cross_section.to_string())

results_df.to_csv(FACTOR_DIR / "ai_betas_v2.csv", index=False)
print(f"\nwrote {FACTOR_DIR / 'ai_betas_v2.csv'} ({len(results_df)} rows)")
