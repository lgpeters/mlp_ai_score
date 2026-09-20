"""Daily close prices for the universe, from yfinance.

closeprice is yfinance's auto_adjust=True Close -- adjusted for both stock
splits and dividends, not the raw historical close. Raw close would make
a stock split look like a 90%+ price crash on the split date (see NVDA's
2024 10:1 split), which is wrong for any time-series use. Verified against
real known split/dividend history before building this (NVDA's 2000,
2001, 2006, 2007, 2021, 2024 splits and 56 dividend events all landed on
the correct dates with the correct ratios).

Stooq was evaluated as a second free source but its CSV endpoint is now
behind a JavaScript bot-challenge that plain HTTP requests can't get
through -- yfinance alone is what this pipeline uses.

Also pulls a handful of reference series alongside the universe: three
AI-themed ETFs as an off-the-shelf AI-exposure proxy (tracking their price
directly, not their holdings -- ARK's funds were considered and
explicitly ruled out), and the S&P 500 as a market benchmark.
"""

import yfinance as yf

from shared.db import get_connection

# AI-themed ETFs (price-only proxy, not holdings-based) + S&P 500 benchmark.
# Same "prices" table as the universe -- ticker/date/closeprice is source-
# agnostic, no FK to companies, so these just live alongside the universe.
REFERENCE_TICKERS = [
    "BOTZ",   # Global X Robotics & Artificial Intelligence ETF
    "AIQ",    # Global X Artificial Intelligence & Technology ETF
    "IRBO",   # iShares Robotics and Artificial Intelligence Multisector ETF
    "^GSPC",  # S&P 500
]

UPSERT_SQL = """
    insert into prices (ticker, date, closeprice)
    values (%s, %s, %s)
    on conflict (ticker, date) do update set closeprice = excluded.closeprice
"""


def get_tickers() -> list[str]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select ticker from companies order by ticker")
            return [row[0] for row in cur.fetchall()] + REFERENCE_TICKERS


def fetch_one(ticker: str) -> int:
    df = yf.Ticker(ticker).history(period="max", auto_adjust=True)
    if df.empty:
        return 0

    rows = [(ticker, ts.date(), float(close)) for ts, close in df["Close"].items()]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)

    return len(rows)


def run() -> None:
    tickers = get_tickers()
    print(f"{len(tickers)} tickers to fetch")

    for ticker in tickers:
        n = fetch_one(ticker)
        print(f"  {ticker}: {n} rows")


if __name__ == "__main__":
    run()
