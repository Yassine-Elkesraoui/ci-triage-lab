"""Day 14 — Evaluation runner.

For each test question (EN/FR/PT), sends it to BOTH the baseline /chat
endpoint and the RAG /chat/rag endpoint, capturing for each:
  - the generated answer
  - the retrieved contexts (RAG only; needed for RAGAS faithfulness)
  - the grounding flag (RAG only)
  - the end-to-end latency in seconds (for H2)

Writes everything to eval/results/raw_results.json. This script does NOT
score anything and does NOT call any external API — it only collects raw
outputs from the local system. Scoring (Gemini/RAGAS) happens in score_eval.py.

Prerequisites:
  - uvicorn running (api.main:app)
  - Ollama running with phi3
  - ingest_corpus.py already run (corpus in the vector store)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

API_BASE = "http://127.0.0.1:8000"
QUESTIONS_PATH = Path("eval/test_questions.json")
RESULTS_DIR = Path("eval/results")
RESULTS_PATH = RESULTS_DIR / "raw_results.json"

# Per-call timeout. Phi-3 on CPU can take 15-30s per generation, so we
# allow generous headroom to avoid spurious timeouts on long answers.
CALL_TIMEOUT = 180


def call_baseline(question: str) -> dict:
    """Call POST /chat. Returns answer text + latency."""
    start = time.perf_counter()
    resp = requests.post(
        f"{API_BASE}/chat",
        json={"question": question},
        timeout=CALL_TIMEOUT,
    )
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    body = resp.json()
    return {
        "answer": body["answer"],
        "latency_s": round(elapsed, 3),
    }


def call_rag(question: str) -> dict:
    """Call POST /chat/rag. Returns answer, grounding, contexts + latency."""
    start = time.perf_counter()
    resp = requests.post(
        f"{API_BASE}/chat/rag",
        json={"question": question, "document_id": None, "top_k": 4},
        timeout=CALL_TIMEOUT,
    )
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    body = resp.json()
    contexts = [s["content"] for s in body.get("sources", [])]
    return {
        "answer": body["answer"],
        "grounded": body["grounded"],
        "contexts": contexts,
        "num_sources": len(contexts),
        "latency_s": round(elapsed, 3),
    }


def main() -> None:
    print("=" * 60)
    print("Day 14 — Evaluation runner")
    print("=" * 60)

    if not QUESTIONS_PATH.exists():
        print(f"FAIL: {QUESTIONS_PATH} not found.")
        sys.exit(1)
    data = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions = data["questions"]
    languages = data["metadata"]["languages"]

    # Health check
    try:
        h = requests.get(f"{API_BASE}/docs", timeout=5)
        if h.status_code != 200:
            print(f"API responded HTTP {h.status_code}. Is uvicorn running?")
            sys.exit(1)
    except requests.exceptions.RequestException:
        print(f"Cannot reach API at {API_BASE}. Start uvicorn first.")
        sys.exit(1)

    total = len(questions) * len(languages)
    print(f"Running {len(questions)} questions x {len(languages)} languages "
          f"= {total} prompts, each through 2 endpoints ({total * 2} calls).")
    print("Phi-3 on CPU is slow; expect this to take several minutes.\n")

    records = []
    counter = 0
    for q in questions:
        for lang in languages:
            counter += 1
            question_text = q[lang]
            print(f"[{counter}/{total}] {q['id']} ({lang}): {question_text[:55]}...")

            try:
                baseline = call_baseline(question_text)
                print(f"    baseline: {baseline['latency_s']}s")
            except Exception as exc:
                print(f"    baseline FAILED: {exc}")
                baseline = {"answer": None, "latency_s": None, "error": str(exc)}

            try:
                rag = call_rag(question_text)
                flag = "grounded" if rag["grounded"] else "UNGROUNDED"
                print(f"    rag:      {rag['latency_s']}s  [{flag}, {rag['num_sources']} sources]")
            except Exception as exc:
                print(f"    rag FAILED: {exc}")
                rag = {"answer": None, "grounded": None, "contexts": [],
                       "num_sources": 0, "latency_s": None, "error": str(exc)}

            records.append({
                "question_id": q["id"],
                "language": lang,
                "question": question_text,
                "answerable": q["answerable"],
                "baseline": baseline,
                "rag": rag,
            })

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "meta": {
            "api_base": API_BASE,
            "num_questions": len(questions),
            "languages": languages,
            "total_prompts": total,
        },
        "records": records,
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"Wrote {RESULTS_PATH} with {len(records)} records.")
    print("Raw collection complete. Next: score_eval.py (RAGAS faithfulness).")


if __name__ == "__main__":
    main()
