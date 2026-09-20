import os

import requests
from dotenv import load_dotenv

load_dotenv()

BUCKET = "markdown_content"


def _base_url() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/storage/v1"


def _headers(**extra) -> dict:
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return {"Authorization": f"Bearer {key}", "apikey": key, **extra}


def upload_text(path: str, content: str, content_type: str = "text/markdown") -> None:
    resp = requests.put(
        f"{_base_url()}/object/{BUCKET}/{path}",
        headers=_headers(**{"Content-Type": f"{content_type}; charset=utf-8", "x-upsert": "true"}),
        data=content.encode("utf-8"),
    )
    resp.raise_for_status()


def download_text(path: str) -> str:
    resp = requests.get(f"{_base_url()}/object/{BUCKET}/{path}", headers=_headers())
    resp.raise_for_status()
    return resp.content.decode("utf-8")


def delete(path: str) -> None:
    resp = requests.delete(f"{_base_url()}/object/{BUCKET}/{path}", headers=_headers())
    resp.raise_for_status()


def get_chunk_content(storage_path: str, start_offset: int, end_offset: int) -> str:
    return download_text(storage_path)[start_offset:end_offset]
