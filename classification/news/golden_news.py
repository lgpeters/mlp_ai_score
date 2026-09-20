"""Generates a CSV of randomly sampled hackernews chunks for the user to
hand-score end to end (all chunk_classifications judgment columns), as a
manual benchmark to check the LLM passes against later. Includes every
judgment column on chunk_classifications -- both cheap-pass
(company_relevant, ai_associated) and the columns deferred to the second,
smarter-model pass (ai_category, ai_materiality_score, ai_saliency_score)
-- plus vader_compound for reference, since it's the blind-sentiment
baseline this benchmark is partly meant to compare against."""

from pathlib import Path

from shared.db import execute_df
from shared.storage import get_chunk_content

SAMPLE_SQL = """
    select ci.chunk_id, ci.content_id, co.ticker, co.name as company_name,
           c.publication_date, c.source_doc_id as url,
           cc.company_relevant, cc.ai_associated,
           cc.ai_category, cc.ai_materiality_score, cc.ai_saliency_score,
           cc.vader_compound,
           ci.start_offset, ci.end_offset
    from content_index ci
    join content c using (content_id)
    join companies co using (company_id)
    left join chunk_classifications cc using (chunk_id)
    where c.type = 'hackernews'
    order by random()
    limit %(n)s
"""

OUT_PATH = Path(__file__).resolve().parent / "golden_news.csv"


def run(n: int = 200) -> None:
    df = execute_df(SAMPLE_SQL, params={"n": n})
    df["title"] = df.apply(
        lambda row: get_chunk_content(row["content_id"], "hackernews", None, row["start_offset"], row["end_offset"]),
        axis=1,
    )
    df = df.drop(columns=["start_offset", "end_offset", "content_id"])
    df = df[[
        "company_name", "ticker", "chunk_id", "title", "publication_date", "url",
        "company_relevant", "ai_associated",
        "ai_category", "ai_materiality_score", "ai_saliency_score", "vader_compound",
    ]]
    df.to_csv(OUT_PATH, index=False)
    print(f"{len(df)} golden news chunks written to {OUT_PATH}")


if __name__ == "__main__":
    run()
