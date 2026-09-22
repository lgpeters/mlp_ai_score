"""Builds the raw_materiality and raw_sentiment (materiality x salience)
AI-engagement signals, per ticker, 365D rolling, for three versions, and
writes them to one wide CSV with a multi-level column header:

    (version, feature, ticker)

Versions:
    ALL    -- every AI-relevant chunk (ai_associated in {1,2,3})
    ASSOC1 -- only ai_associated == 1 (General AI) counts toward the
              numerator; everything else is zeroed
    ASSOC2 -- only ai_associated == 2 (AI Infra) counts toward the
              numerator; everything else is zeroed

The denominator (total_chunks per ticker/date, rolled 365D) is the SAME
across every version -- it's the full count of chunks stage 1 considered,
never reduced by the masking. Masking only zeroes the numerator columns
(ai_materiality/ai_salience/ai_sentiment/AI) for chunks outside the
version's scope; it never drops rows, so `total_chunks` (a row count) is
unaffected by which version is being computed.

Usage: python3 build_signals.py [output_csv]
Default output: signals_multiindex.csv (this directory).
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from shared.db import get_connection

CONTENT_TYPES = ["sec_filing", "transcripts", "hackernews"]

ROMAN_TO_INT = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
INSTRUCTIONS_PATH = REPO_ROOT / "classification" / "second_pass_prompts" / "SecondPassInstructions.md"


def parse_category_labels() -> dict[int, str]:
    """Reads the real ai_category (1-6) label names straight from the
    actual prompt file, so a rename there doesn't silently drift out of
    sync with hardcoded labels elsewhere."""
    text = INSTRUCTIONS_PATH.read_text()
    labels = {}
    for roman, label in re.findall(r"\((i{1,3}|iv|v|vi)\)\s+([^:\n]+):", text):
        labels[ROMAN_TO_INT[roman]] = label.strip()
    return labels
ROLL_WINDOW = "365D"

WIDE_QUERY_SQL = """
    SELECT
        co.ticker,
        c.content_id,
        c.type,
        c.sub_type,
        c.publication_date::date AS published_date,
        cc.ai_associated,
        cc.ai_category AS ai_group,
        cc.ai_materiality_score AS ai_materiality,
        cc.ai_saliency_score AS ai_salience,
        cc.is_boilerplate_risk
    FROM chunk_classifications cc
    JOIN content_index ci USING (chunk_id)
    JOIN content c USING (content_id)
    JOIN companies co USING (company_id)
    WHERE c.publication_date >= '2012-01-01'
      AND (
        (c.type = 'sec_filing' AND c.sub_type IN ('10-K','10-K/A','10-Q','10-Q/A','20-F','20-F/A','6-K','6-K/A'))
        OR (c.type = 'transcripts' AND c.sub_type = 'earnings_call')
        OR (c.type = 'hackernews')
      )
"""


def load_raw() -> pd.DataFrame:
    with get_connection() as conn:
        df = pd.read_sql_query(WIDE_QUERY_SQL, conn)
    df["published_date"] = pd.to_datetime(df["published_date"])
    df = df.sort_values(["ticker", "published_date"])
    df[["ai_associated", "ai_materiality", "ai_salience"]] = df[
        ["ai_associated", "ai_materiality", "ai_salience"]
    ].fillna(0)
    df["ai_sentiment"] = df["ai_materiality"] * df["ai_salience"]
    df["AI"] = np.where(df["ai_associated"] >= 1, 1, 0)
    # is_boilerplate_risk is only ever populated for SEC "Clear Negative" (category
    # 5) chunks -- everything else (never boilerplate-checked) defaults to False,
    # so it's excluded from the Boilerplate Risk version rather than propagating NaN.
    df["is_boilerplate_risk"] = df["is_boilerplate_risk"].fillna(False).astype(bool)
    return df


def masked_copy(df_raw: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    """Zero the numerator columns for chunks outside `mask`. Rows are
    never dropped, so a downstream row-count denominator is unaffected."""
    out = df_raw.copy()
    out.loc[~mask, ["ai_materiality", "ai_salience", "ai_sentiment", "AI"]] = 0
    return out


def build_version(df_masked: pd.DataFrame) -> pd.DataFrame:
    """Full pipeline: per-document sums -> daily sums -> 365D rolling sums
    -> raw_materiality/raw_salience/raw_sentiment ratios, pivoted to
    (published_date x ticker) per feature. Returns a dict of feature -> matrix."""
    df_docs = df_masked.groupby(
        ["ticker", "content_id", "type", "sub_type", "published_date"]
    ).agg(
        ai_materiality_sum=("ai_materiality", "sum"),
        ai_salience_sum=("ai_salience", "sum"),
        ai_sentiment_sum=("ai_sentiment", "sum"),
        ai_chunks=("AI", "sum"),
        total_chunks=("ticker", "count"),
    ).reset_index()

    daily = (
        df_docs[df_docs["type"].isin(CONTENT_TYPES)]
        .groupby(["ticker", "published_date"])[
            ["ai_materiality_sum", "ai_salience_sum", "ai_sentiment_sum", "ai_chunks", "total_chunks"]
        ]
        .sum()
        .reset_index()
        .sort_values(["ticker", "published_date"])
    )

    rolling = (
        daily.groupby("ticker")
        .rolling(ROLL_WINDOW, on="published_date")[
            ["ai_materiality_sum", "ai_salience_sum", "ai_sentiment_sum", "ai_chunks", "total_chunks"]
        ]
        .sum()
        .reset_index()
    )

    rolling["raw_materiality"] = rolling["ai_materiality_sum"] / rolling["total_chunks"]
    rolling["raw_salience"] = rolling["ai_salience_sum"] / rolling["total_chunks"]
    rolling["raw_sentiment"] = rolling["ai_sentiment_sum"] / rolling["total_chunks"]

    matrices = {}
    for feature in ["raw_materiality", "raw_salience", "raw_sentiment"]:
        matrices[feature] = rolling.pivot(
            index="published_date", columns="ticker", values=feature
        ).ffill()
    return matrices


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "signals_multiindex.csv"

    print("querying database...")
    df_raw = load_raw()

    versions: dict[str, pd.Series] = {
        "ALL": (df_raw["ai_associated"] >= 1) & ~df_raw["is_boilerplate_risk"],
        "ASSOC1": df_raw["ai_associated"] == 1,
        "ASSOC2": df_raw["ai_associated"] == 2,
        "Boilerplate Risk": df_raw["is_boilerplate_risk"],
    }
    category_labels = parse_category_labels()
    # categories 4 (Clear Positive) and 5 (Clear Negative) folded into one --
    # direction is already captured by ai_salience/ai_sentiment, so splitting
    # by direction here was double-counting what valence already tells you.
    merged_groups = {4, 5}
    for g, label in category_labels.items():
        if g in merged_groups:
            continue
        versions[label] = df_raw["ai_group"] == g
    versions["Financial Exposure"] = df_raw["ai_group"].isin(merged_groups) & ~df_raw["is_boilerplate_risk"]

    all_frames = []
    for version_name, mask in versions.items():
        print(f"building version: {version_name} ({mask.sum()} matching chunks)")
        matrices = build_version(masked_copy(df_raw, mask))
        for feature in ["raw_materiality", "raw_sentiment"]:
            matrix = matrices[feature].copy()
            matrix.columns = pd.MultiIndex.from_product(
                [[version_name], [feature], matrix.columns], names=["version", "feature", "ticker"]
            )
            all_frames.append(matrix)

    wide = pd.concat(all_frames, axis=1).sort_index()
    wide.to_csv(output_path)
    print(f"wrote {output_path} ({wide.shape[0]} rows x {wide.shape[1]} columns)")


if __name__ == "__main__":
    main()
