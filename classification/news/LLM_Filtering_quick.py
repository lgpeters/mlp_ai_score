"""Quick, non-batch scorer for the classification/news/golden_news.csv
benchmark -- no SQL, no Batch API (deemed too heavy for just running a
200-row benchmark), plain synchronous Anthropic calls, one per row, using
classification/news/classification_prompt.md as the (cached) system
prompt. Reads golden_news.csv, populates company_relevant/ai_associated,
and writes the result to golden_news_llm.csv -- a separate file, so the
original golden_news.csv (the user's own manual scores) is never
overwritten. This is a benchmark run to check the LLM against the user's
own manual scoring of the same rows -- not a production classification
pass (that's classification/news/LLM_Filtering.py)."""

import os
from pathlib import Path

import pandas as pd
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
PROMPT_PATH = Path(__file__).resolve().parent / "classification_prompt.md"
IN_PATH = Path(__file__).resolve().parent / "golden_news.csv"
OUT_PATH = Path(__file__).resolve().parent / "golden_news_llm.csv"

SYSTEM_PROMPT = PROMPT_PATH.read_text()

TOOL_SCHEMA = {
    "name": "classify_item",
    "description": "Return company-relevance and AI-association classification for the given news item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_relevant": {"type": "integer", "enum": [0, 1]},
            "ai_associated": {"type": "integer", "enum": [0, 1, 2, 3]},
        },
        "required": ["company_relevant", "ai_associated"],
    },
}


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def classify_one(client: Anthropic, company_name: str, title: str) -> tuple[int, int]:
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
        messages=[{"role": "user", "content": f"{company_name}\n{title}"}],
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "classify_item"},
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    company_relevant = tool_use.input["company_relevant"]
    ai_associated = tool_use.input["ai_associated"]

    # Belt-and-braces: tool_choice + enum should already guarantee this, but
    # never trust a model response silently -- same posture as
    # LLM_Filtering.py's _validate_results.
    if company_relevant not in (0, 1) or ai_associated not in (0, 1, 2, 3):
        raise ValueError(f"out-of-range response: company_relevant={company_relevant}, ai_associated={ai_associated}")

    return company_relevant, ai_associated


def run(limit: int | None = None) -> None:
    df = pd.read_csv(IN_PATH)
    indices = df.index[:limit] if limit is not None else df.index

    client = _client()
    failures = 0
    for i in indices:
        row = df.loc[i]
        try:
            company_relevant, ai_associated = classify_one(client, row["company_name"], row["title"])
        except Exception as exc:
            print(f"  [{i}] FAILED ({exc}) -- leaving blank")
            failures += 1
            continue

        df.at[i, "company_relevant"] = company_relevant
        df.at[i, "ai_associated"] = ai_associated
        print(f"  [{i}] {row['company_name']} / {row['title'][:60]!r} -> "
              f"company_relevant={company_relevant}, ai_associated={ai_associated}")

    df.to_csv(OUT_PATH, index=False)
    print(f"\n{len(indices)} rows attempted, {len(indices) - failures} scored, {failures} failed, written to {OUT_PATH}")


if __name__ == "__main__":
    run()
