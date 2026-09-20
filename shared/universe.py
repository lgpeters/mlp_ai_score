from shared.db import get_connection


def get_cik_universe() -> list[tuple[str, str]]:
    """Returns [(cik, company_id), ...] for every CIK that should be tracked,
    including known historical aliases (see company_cik_aliases)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select cik, company_id::text from companies where cik is not null
                union all
                select cik, company_id::text from company_cik_aliases
            """)
            return cur.fetchall()
