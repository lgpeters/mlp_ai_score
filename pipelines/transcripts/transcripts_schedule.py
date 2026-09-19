import re
import time

import requests

from shared.db import get_connection
from shared.ids import content_id_for

BASE_URL = "https://stockanalysis.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DATA_SOURCE = "stockanalysis.com"

# Polite delay between per-ticker requests -- rapid sequential requests
# without one triggered 403s from stockanalysis.com's bot protection.
REQUEST_DELAY_SECONDS = 2

# The list of events (earnings calls, conferences, AGMs, etc.) is embedded as
# a JS object literal inside a <script> tag, not valid JSON (unquoted keys).
# Each entry follows this exact field order.
EVENT_RE = re.compile(
    r'fiscalYear:(\d+),quarterLabel:"([^"]*)",detailSlug:"([^"]*)",'
    r'eventDate:"(\d{4}-\d{2}-\d{2})",eventTitle:"([^"]*)"'
)
EARNINGS_CALL_RE = re.compile(r"^Q[1-4] \d{4}$")

EVENTS_UPSERT_SQL = """
    insert into transcripts (content_id, ticker, fiscal_year, quarter_label, detail_slug, event_date, event_title, is_earnings_call, url)
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    on conflict (ticker, detail_slug) do update set
        content_id = excluded.content_id,
        event_date = excluded.event_date,
        event_title = excluded.event_title,
        is_earnings_call = excluded.is_earnings_call
"""

CONTENT_UPSERT_SQL = """
    insert into content (content_id, company_id, type, sub_type, publication_date, data_source, source_doc_id)
    values (%s, %s, %s, %s, %s, %s, %s)
    on conflict (data_source, source_doc_id) do update set
        sub_type = excluded.sub_type,
        publication_date = excluded.publication_date
"""


def get_universe() -> list[tuple[str, str, str]]:
    """Returns [(ticker, stockanalysis_id, company_id), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select ticker, stockanalysis_id, company_id from companies where stockanalysis_id is not null")
            return cur.fetchall()


def fetch_events(stockanalysis_id: str) -> list[dict]:
    list_url = f"{BASE_URL}/stocks/{stockanalysis_id}/transcripts/"
    resp = requests.get(list_url, headers=HEADERS)
    resp.raise_for_status()

    events = []
    for fiscal_year, quarter_label, detail_slug, event_date, event_title in EVENT_RE.findall(resp.text):
        events.append({
            "fiscal_year": fiscal_year,
            "quarter_label": quarter_label,
            "detail_slug": detail_slug,
            "event_date": event_date,
            "event_title": event_title,
            "is_earnings_call": bool(EARNINGS_CALL_RE.match(event_title)),
            "url": f"{list_url}{detail_slug}/",
        })
    return events


def push_events(ticker: str, company_id: str, events: list[dict]) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            for e in events:
                content_id = content_id_for(DATA_SOURCE, e["url"])
                sub_type = "earnings_call" if e["is_earnings_call"] else "other"

                cur.execute(CONTENT_UPSERT_SQL, (
                    content_id, company_id, "transcripts", sub_type, e["event_date"], DATA_SOURCE, e["url"],
                ))
                cur.execute(EVENTS_UPSERT_SQL, (
                    content_id, ticker, e["fiscal_year"], e["quarter_label"], e["detail_slug"],
                    e["event_date"], e["event_title"], e["is_earnings_call"], e["url"],
                ))


def run() -> None:
    universe = get_universe()

    total = 0
    for i, (ticker, stockanalysis_id, company_id) in enumerate(universe):
        print(f"fetching {ticker} ({stockanalysis_id})...")
        events = fetch_events(stockanalysis_id)
        push_events(ticker, company_id, events)

        total += len(events)
        print(f"  upserted {len(events)} events ({sum(e['is_earnings_call'] for e in events)} earnings calls)")

        if i < len(universe) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\n{total} total events upserted across {len(universe)} companies")


if __name__ == "__main__":
    run()
