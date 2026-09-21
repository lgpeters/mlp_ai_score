"""Pulls the raw AI-engagement signal out of Supabase into pandas, in the
simple flat format requested:

    published_date, ticker, content_type, ai_category, ai_materiality, ai_saliency

Two frames are exposed, because they answer different questions:

- `load_second_pass()`: every chunk that got a real materiality/saliency
  score (the second pass). This is the "how positive/negative and how
  material" signal.
- `load_first_pass()`: every chunk the first pass ever considered, whether
  or not it turned out to be AI-relevant. This is needed to compute
  *intensity* (% of scanned content that was AI-relevant) -- a ratio
  against second_pass alone would trivially be ~100%, since second_pass
  IS the ai_associated>0 subset.

Both are scoped consistently with the rest of the project: Transcripts to
earnings_call only, SEC to 10-K/10-Q/20-F/6-K only (excluding 8-K/S-4,
which never went through either pass).
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import get_connection

SUB_TYPE_FILTERS = {
    "sec_filing": ["10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "6-K", "6-K/A"],
    "transcripts": ["earnings_call"],
    "hackernews": None,
}

CONTENT_TYPE_LABELS = {"sec_filing": "sec", "transcripts": "transcripts", "hackernews": "news"}

SECOND_PASS_SQL = """
    select c.publication_date::date as published_date, co.ticker, c.type as content_type,
           cc.ai_category, cc.ai_materiality_score as ai_materiality, cc.ai_saliency_score as ai_saliency
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where cc.ai_category is not null
      and c.type = %(content_type)s
      and (%(sub_types)s::text[] is null or c.sub_type = any(%(sub_types)s))
      and c.publication_date is not null
"""

FIRST_PASS_SQL = """
    select c.publication_date::date as published_date, co.ticker, c.type as content_type,
           (cc.ai_associated > 0) as ai_relevant
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where cc.ai_associated is not null
      and c.type = %(content_type)s
      and (%(sub_types)s::text[] is null or c.sub_type = any(%(sub_types)s))
      and c.publication_date is not null
"""


def _run_per_type(sql: str) -> pd.DataFrame:
    frames = []
    with get_connection() as conn:
        for content_type, sub_types in SUB_TYPE_FILTERS.items():
            df = pd.read_sql_query(sql, conn, params={"content_type": content_type, "sub_types": sub_types})
            frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["content_type"] = out["content_type"].map(CONTENT_TYPE_LABELS)
    out["published_date"] = pd.to_datetime(out["published_date"])
    return out


def load_second_pass() -> pd.DataFrame:
    """published_date, ticker, content_type, ai_category, ai_materiality, ai_saliency"""
    return _run_per_type(SECOND_PASS_SQL)


def load_first_pass() -> pd.DataFrame:
    """published_date, ticker, content_type, ai_relevant (bool)"""
    return _run_per_type(FIRST_PASS_SQL)


if __name__ == "__main__":
    sp = load_second_pass()
    fp = load_first_pass()
    print("second_pass:", sp.shape, sp["published_date"].min(), "->", sp["published_date"].max())
    print(sp.head())
    print("\nfirst_pass:", fp.shape)
    print(fp.groupby("content_type")["ai_relevant"].mean())
