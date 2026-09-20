"""Daily Fama-French factors + momentum, from Kenneth French's data
library (Dartmouth), fetched directly as CSV-in-zip -- no pandas-datareader
dependency needed, the source files parse fine with a small header-finder.

All values stored are PERCENT, exactly as published (0.09 means 0.09%).

mkt_rf/rf come from the 3-factor file and are verified byte-identical to
the 5-factor file's own Mkt-RF/RF across the full overlapping history.
smb/hml are NOT shared between the two files -- the 5-factor library's
SMB/HML use a different sort methodology than the 3-factor library's, so
they're kept as separate smb_3/hml_3 vs smb_5/hml_5 columns.

NOTE: despite being daily-frequency data, Kenneth French's library is
only refreshed roughly monthly -- expect the latest row here to lag
"today" by several weeks. This isn't a bug in this fetcher; the source
itself lags.
"""

import io
import zipfile

import pandas as pd
import requests

from shared.db import get_connection

HEADERS = {"User-Agent": "Mozilla/5.0"}

URLS = {
    "3factor": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip",
    "5factor": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
    "momentum": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip",
}

UPSERT_SQL = """
    insert into factors (date, mkt_rf, rf, smb_3, hml_3, smb_5, hml_5, rmw, cma, mom)
    values (%(date)s, %(mkt_rf)s, %(rf)s, %(smb_3)s, %(hml_3)s, %(smb_5)s, %(hml_5)s, %(rmw)s, %(cma)s, %(mom)s)
    on conflict (date) do update set
        mkt_rf = coalesce(excluded.mkt_rf, factors.mkt_rf),
        rf     = coalesce(excluded.rf, factors.rf),
        smb_3  = coalesce(excluded.smb_3, factors.smb_3),
        hml_3  = coalesce(excluded.hml_3, factors.hml_3),
        smb_5  = coalesce(excluded.smb_5, factors.smb_5),
        hml_5  = coalesce(excluded.hml_5, factors.hml_5),
        rmw    = coalesce(excluded.rmw, factors.rmw),
        cma    = coalesce(excluded.cma, factors.cma),
        mom    = coalesce(excluded.mom, factors.mom)
"""


def _fetch_csv(url: str) -> pd.DataFrame:
    """Downloads the zip, finds the real CSV header (the description text
    at the top varies in length across these files, so detected rather
    than assumed at a fixed line number), and reads data rows up to the
    first non-date row (the copyright footer, or an annual-data section
    on non-daily files)."""
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    lines = text.splitlines()

    header_idx = next(i for i, l in enumerate(lines) if l.strip().startswith(","))
    cols = ["date"] + [c.strip() for c in lines[header_idx].split(",")[1:]]

    data_lines = []
    for l in lines[header_idx + 1:]:
        first_field = l.split(",")[0].strip()
        if not (first_field.isdigit() and len(first_field) == 8):
            break
        data_lines.append(l)

    df = pd.read_csv(io.StringIO("\n".join(data_lines)), header=None, names=cols)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.date
    return df


def run() -> None:
    df3 = _fetch_csv(URLS["3factor"]).rename(columns={"SMB": "smb_3", "HML": "hml_3", "RF": "rf", "Mkt-RF": "mkt_rf"})
    df5 = _fetch_csv(URLS["5factor"]).rename(
        columns={"SMB": "smb_5", "HML": "hml_5", "RMW": "rmw", "CMA": "cma"}
    )[["date", "smb_5", "hml_5", "rmw", "cma"]]
    dfm = _fetch_csv(URLS["momentum"]).rename(columns={"Mom": "mom"})

    merged = df3.merge(df5, on="date", how="outer").merge(dfm, on="date", how="outer")
    merged = merged.where(pd.notnull(merged), None)

    rows = merged.to_dict("records")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)

    print(f"{len(rows)} rows upserted, {merged['date'].min()} to {merged['date'].max()}")


if __name__ == "__main__":
    run()
