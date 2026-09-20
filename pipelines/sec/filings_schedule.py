import pandas as pd

from shared.db import get_connection
from shared.edgar import fetch_all_filings
from shared.ids import content_id_for
from shared.universe import get_cik_universe

FORM_TYPES = [
    "8-K", "10-Q", "10-K", "S-4", "8-K/A", "10-K/A", "10-Q/A", "S-4/A", "20-F", "20-F/A",
    # 6-K: the interim/quarterly-equivalent report for a 20-F foreign private
    # issuer (no 10-Q equivalent exists for them). Relevant here only for
    # TEAM/Atlassian, which filed 20-F 2016-2022 while still UK-incorporated
    # before redomiciling to Delaware and switching to 10-K. Unlike 8-K, 6-K
    # has no standardized SEC "items" taxonomy to filter on, so it's not
    # added to EIGHT_K_FORMS -- every 6-K that's filed passes through, same
    # as 10-Q/10-K/20-F.
    "6-K", "6-K/A",
]
START_DATE = "2012-01-01"
DATA_SOURCE = "sec_edgar"

# 8-K is high-volume, and most items are purely administrative/legal
# (governance mechanics, listing compliance, auditor changes, vote results)
# with no business-strategy narrative. The items below are the ones where a
# company would plausibly signal active AI engagement:
#   1.01  Entry into a material agreement      -- AI partnerships/licensing/JVs
#   2.01  Completion of an acquisition          -- a closed AI acquisition
#   2.02  Results of operations (earnings)      -- MD&A-style commentary
#   5.02  Officer appointments/departures       -- e.g. a "Chief AI Officer" hire
#   7.01  Reg FD disclosure                     -- investor-day AI strategy decks
#   8.01  Other events                          -- product launches, AI partnership news
# Excluded as low-value for this purpose: 3.01-3.03 (listing/securities
# compliance), 4.01 (auditor changes), 5.01/5.03-5.08 (governance mechanics,
# bylaws, vote results), 6.xx (asset-backed securities administrivia).
EIGHT_K_FORMS = {"8-K", "8-K/A"}
EIGHT_K_RELEVANT_ITEMS = {"1.01", "2.01", "2.02", "5.02", "7.01", "8.01"}


def _has_relevant_item(items: str | None) -> bool:
    if not items:
        return False
    return bool(EIGHT_K_RELEVANT_ITEMS & {item.strip() for item in items.split(",")})

CONTENT_UPSERT_SQL = """
    insert into content (
        content_id, company_id, type, sub_type, publication_date,
        data_source, source_doc_id, processed
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (data_source, source_doc_id) do update set
        sub_type = excluded.sub_type,
        publication_date = excluded.publication_date
"""

SEC_FILINGS_UPSERT_SQL = """
    insert into sec_filings (
        content_id, accession_number, filing_date, report_date,
        acceptance_date_time, act, form, file_number, film_number,
        items, core_type, size, is_xbrl, is_inline_xbrl, is_xbrl_numeric,
        primary_document, primary_doc_description
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (content_id) do update set
        filing_date = excluded.filing_date,
        report_date = excluded.report_date,
        acceptance_date_time = excluded.acceptance_date_time
"""


def get_filings(cik: str) -> pd.DataFrame:
    df = pd.DataFrame(fetch_all_filings(cik))
    df = df[df["form"].isin(FORM_TYPES)].copy()

    is_eight_k = df["form"].isin(EIGHT_K_FORMS)
    has_relevant_item = df["items"].apply(_has_relevant_item)
    df = df[~is_eight_k | has_relevant_item]

    df["filingDate"] = pd.to_datetime(df["filingDate"], format="%Y-%m-%d").dt.date
    df["reportDate"] = pd.to_datetime(df["reportDate"], format="%Y-%m-%d", errors="coerce").dt.date
    df["acceptanceDateTime"] = pd.to_datetime(df["acceptanceDateTime"])

    cutoff = pd.Timestamp(START_DATE, tz=df["acceptanceDateTime"].dt.tz)
    df = df[df["acceptanceDateTime"] > cutoff].sort_values(by="acceptanceDateTime", ascending=False)

    return df.where(pd.notnull(df), None)


def push_filings_to_db(company_id: str, df_filings: pd.DataFrame) -> None:
    if df_filings.empty:
        return

    content_rows = []
    filing_rows = []

    for row in df_filings.to_dict("records"):
        content_id = content_id_for(DATA_SOURCE, row["accessionNumber"])

        content_rows.append((
            content_id, company_id, "sec_filing", row["form"], row["acceptanceDateTime"],
            DATA_SOURCE, row["accessionNumber"], False,
        ))

        filing_rows.append((
            content_id, row["accessionNumber"], row["filingDate"], row["reportDate"],
            row["acceptanceDateTime"], row["act"], row["form"], row["fileNumber"],
            row["filmNumber"], row["items"], row["core_type"], row["size"],
            bool(row["isXBRL"]) if row["isXBRL"] is not None else None,
            bool(row["isInlineXBRL"]) if row["isInlineXBRL"] is not None else None,
            bool(row["isXBRLNumeric"]) if row["isXBRLNumeric"] is not None else None,
            row["primaryDocument"], row["primaryDocDescription"],
        ))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(CONTENT_UPSERT_SQL, content_rows)
            cur.executemany(SEC_FILINGS_UPSERT_SQL, filing_rows)

    print(f"  upserted {len(content_rows)} filings")


def run() -> None:
    for cik, company_id in get_cik_universe():
        print(f"Fetching filings schedule for CIK: {cik}")
        df_filings = get_filings(cik)
        push_filings_to_db(company_id, df_filings)


if __name__ == "__main__":
    run()
