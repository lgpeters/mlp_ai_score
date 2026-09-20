from pipelines import load_universe
from pipelines.indexing import chunking, finlang_embedding
from pipelines.sec import company_info, filings_schedule
from pipelines.sec import download_markdown as sec_download_markdown
from pipelines.transcripts import download_markdown as transcripts_download_markdown
from pipelines.transcripts import transcripts_schedule

# Ordered end-to-end run. Each module exposes a `run()` entrypoint.
# Extend this list as new pipeline steps come online, e.g.:
#   from pipelines.news import download_content as news_download_content
STEPS = [
    ("load_universe", load_universe),
    ("sec.company_info", company_info),
    ("sec.filings_schedule", filings_schedule),
    ("transcripts.transcripts_schedule", transcripts_schedule),
]

# Slow, rate-limited bulk downloads -- skipped by default so a normal
# run_all() stays a quick metadata refresh. Pass download_markdown=True to
# include them.
DOWNLOAD_STEPS = [
    ("sec.download_markdown", sec_download_markdown),
    ("transcripts.download_markdown", transcripts_download_markdown),
]

# Chunk + embed newly-downloaded content. Depends on DOWNLOAD_STEPS having
# already run at some point (chunking only picks up rows with a
# storage_path). Pass index=True to include.
INDEX_STEPS = [
    ("indexing.chunking", chunking),
    ("indexing.finlang_embedding", finlang_embedding),
]


def run_all(download_markdown: bool = False, index: bool = False) -> None:
    steps = STEPS
    if download_markdown:
        steps = steps + DOWNLOAD_STEPS
    if index:
        steps = steps + INDEX_STEPS

    for name, step in steps:
        print(f"=== {name} ===")
        step.run()


if __name__ == "__main__":
    run_all()
