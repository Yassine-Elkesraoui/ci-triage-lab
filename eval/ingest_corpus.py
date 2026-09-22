"""Day 14 — Corpus ingestion for evaluation.

Uploads and processes the three parallel Cloud-computing articles
(EN/FR/PT) into the running RAG system via its public HTTP API, then
records the resulting document UUIDs to eval/corpus_index.json.

Run this ONCE, with the API server running (uvicorn), before run_eval.py.
Re-running is safe: processing is idempotent at the vector-store layer,
but it will create new document UUIDs each upload, so the index file is
overwritten with the latest run's UUIDs.

This script talks ONLY to the local system. No external API is used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

API_BASE = "http://127.0.0.1:8000"
CORPUS_DIR = Path("eval/corpus")
INDEX_PATH = Path("eval/corpus_index.json")

CORPUS_FILES = {
    "en": "cloud_computing_en.txt",
    "fr": "cloud_computing_fr.txt",
    "pt": "cloud_computing_pt.txt",
}


def ingest_one(lang: str, filename: str) -> dict:
    """Upload and process a single corpus file. Returns its index entry."""
    path = CORPUS_DIR / filename
    if not path.exists():
        print(f"  FAIL: {path} not found.")
        sys.exit(1)

    # 1. Upload
    with path.open("rb") as fh:
        files = {"file": (filename, fh, "text/plain")}
        resp = requests.post(f"{API_BASE}/documents", files=files, timeout=60)
    if resp.status_code != 201:
        print(f"  FAIL upload ({lang}): HTTP {resp.status_code} {resp.text}")
        sys.exit(1)
    doc_id = resp.json()["id"]
    print(f"  [{lang}] uploaded  -> {doc_id}")

    # 2. Process (parse + chunk + embed + persist)
    resp = requests.post(f"{API_BASE}/documents/{doc_id}/process", timeout=300)
    if resp.status_code != 200:
        print(f"  FAIL process ({lang}): HTTP {resp.status_code} {resp.text}")
        sys.exit(1)
    body = resp.json()
    chunk_count = body.get("chunk_count", "?")
    print(f"  [{lang}] processed -> {chunk_count} chunks")

    return {
        "lang": lang,
        "filename": filename,
        "document_id": doc_id,
        "chunk_count": chunk_count,
    }


def main() -> None:
    print("=" * 60)
    print("Day 14 — Corpus ingestion")
    print("=" * 60)

    # Health check
    try:
        h = requests.get(f"{API_BASE}/docs", timeout=5)
        if h.status_code != 200:
            print(f"API at {API_BASE} responded HTTP {h.status_code}. Is uvicorn running?")
            sys.exit(1)
    except requests.exceptions.RequestException:
        print(f"Cannot reach API at {API_BASE}. Start uvicorn first:")
        print("  uvicorn api.main:app --reload")
        sys.exit(1)

    index = {"api_base": API_BASE, "documents": []}
    for lang, filename in CORPUS_FILES.items():
        entry = ingest_one(lang, filename)
        index["documents"].append(entry)

    INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")
    print()
    print(f"Wrote {INDEX_PATH} with {len(index['documents'])} documents.")
    print("Ingestion complete. You can now run run_eval.py.")


if __name__ == "__main__":
    main()
