import csv
import re
from pathlib import Path

from shared.db import get_connection

UNIVERSE_CSV = Path(__file__).resolve().parent.parent / "data" / "universe.csv"

NAME_TICKER_RE = re.compile(r"^(.*)\s+\(([A-Z.]+)\)$")

UPSERT_SQL = """
    insert into companies (cik, ticker, name, stockanalysis_id)
    values (%s, %s, %s, %s)
    on conflict (cik) do update set
        ticker = excluded.ticker,
        name = excluded.name,
        stockanalysis_id = excluded.stockanalysis_id
"""


def parse_row(raw_name: str, raw_cik: str) -> tuple[str, str, str, str]:
    match = NAME_TICKER_RE.match(raw_name.strip())
    if not match:
        raise ValueError(f"Could not extract ticker from company name: {raw_name!r}")
    name, ticker = match.groups()
    cik = raw_cik.strip()
    stockanalysis_id = ticker.lower()
    return cik, ticker, name.strip(), stockanalysis_id


def run() -> None:
    with UNIVERSE_CSV.open(newline="") as f:
        rows = [parse_row(row["CompanyName"], row["CIK"]) for row in csv.DictReader(f)]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)

    print(f"Upserted {len(rows)} companies.")


if __name__ == "__main__":
    run()
