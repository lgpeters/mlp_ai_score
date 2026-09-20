from psycopg2.extras import Json

from shared.db import get_connection
from shared.edgar import fetch_submissions
from shared.universe import get_cik_universe

FIELDS = [
    "entityType",
    "sic",
    "sicDescription",
    "ownerOrg",
    "name",
    "tickers",
    "exchanges",
    "ein",
    "lei",
    "description",
    "website",
    "investorWebsite",
    "category",
    "fiscalYearEnd",
    "stateOfIncorporation",
    "stateOfIncorporationDescription",
    "formerNames",
]

COLUMNS = [
    "cik",
    "entity_type",
    "sic",
    "sic_description",
    "owner_org",
    "name",
    "tickers",
    "exchanges",
    "ein",
    "lei",
    "description",
    "website",
    "investor_website",
    "category",
    "fiscal_year_end",
    "state_of_incorporation",
    "state_of_incorporation_description",
    "former_names",
]

UPSERT_SQL = f"""
    insert into sec_companies ({", ".join(COLUMNS)})
    values ({", ".join(["%s"] * len(COLUMNS))})
    on conflict (cik) do update set
        {", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c != "cik")},
        retrieved_at = now()
"""


def run() -> None:
    ciks = [cik for cik, _ in get_cik_universe()]

    rows = []
    for cik in ciks:
        submissions = fetch_submissions(cik)
        values = [submissions["cik"]]
        for field in FIELDS:
            value = submissions.get(field)
            if field == "formerNames":
                value = Json(value)
            values.append(value)
        rows.append(tuple(values))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)

    print(f"Upserted {len(rows)} sec_companies rows.")


if __name__ == "__main__":
    run()
