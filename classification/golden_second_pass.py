"""Golden sample for the second (category/materiality/saliency) pass --
20 random chunks each from news/sec/transcripts, restricted to rows that
already cleared the first pass (company_relevant = true, ai_associated > 0),
since that's the only population the second pass ever sees. Content is
the chunk's actual text -- the HN title for news, the paragraph excerpt
for sec/transcripts -- for manual scoring before trusting a real run."""

from pathlib import Path

from shared.db import execute_df
from shared.storage import get_chunk_content

SAMPLE_SQL = """
    select ci.chunk_id, ci.content_id, co.ticker, co.name as company_name,
           c.publication_date, c.storage_path, ci.start_offset, ci.end_offset
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where c.type = %(content_type)s
      and cc.company_relevant = true and cc.ai_associated > 0
    order by random()
    limit %(n)s
"""

OUT_PATH = Path(__file__).resolve().parent / "golden_second_pass.csv"

SOURCES = {"news": "hackernews", "sec": "sec_filing", "transcripts": "transcripts"}


def run(n_per_source: int = 20) -> None:
    frames = []
    for label, content_type in SOURCES.items():
        df = execute_df(SAMPLE_SQL, params={"content_type": content_type, "n": n_per_source})
        df["source"] = label
        df["content"] = df.apply(
            lambda row: get_chunk_content(
                row["content_id"], content_type, row["storage_path"], row["start_offset"], row["end_offset"]
            ),
            axis=1,
        )
        frames.append(df)

    import pandas as pd
    out = pd.concat(frames, ignore_index=True)
    out = out.drop(columns=["content_id", "storage_path", "start_offset", "end_offset"])
    out["ai_category"] = None
    out["ai_materiality_score"] = None
    out["ai_saliency_score"] = None
    out = out[[
        "source", "ticker", "company_name", "chunk_id", "publication_date", "content",
        "ai_category", "ai_materiality_score", "ai_saliency_score",
    ]]
    out.to_csv(OUT_PATH, index=False)
    print(f"{len(out)} rows ({n_per_source} per source) written to {OUT_PATH}")


if __name__ == "__main__":
    run()
