"""Day 14 - RAGAS faithfulness scorer (Cerebras / gpt-oss-120b judge).

Computes RAGAS faithfulness for BOTH the RAG answer and the baseline
answer, each judged against the SAME retrieved contexts (the standard
RAG-vs-no-RAG comparison; the baseline answers from parametric memory so
its claims are less supported by context -> lower faithfulness = H1 effect).

UNGROUNDED RAG cases (no contexts) cannot be scored -> recorded as None,
reported separately as abstention cases.

Judge: Cerebras free tier gpt-oss-120b. Output token cap raised so
long baseline answers can be fully decomposed (default cap truncated the
judge's statement-extraction JSON, which failed baseline scoring). A 70B
judge is competent multilingually, protecting the H3 comparison. Only this
offline scorer calls Cerebras; the system under test stays fully local.

Usage:
    python eval/score_eval.py <raw.json> <out.json> <label>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

JUDGE_MODEL = "gpt-oss-120b"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"

# Raise the judge's max output tokens so long baseline answers can be fully
# decomposed into claims without truncating the JSON response.
JUDGE_MAX_TOKENS=3000
JUDGE_TEMPERATURE = 0.0  # deterministic judging

SLEEP_BETWEEN_SAMPLES_S = 8.0
HARD_429_WAIT_S = 60.0


def build_judge():
    """RAGAS LLM wrapper around Cerebras, with raised max_tokens for the judge."""
    from openai import OpenAI
    from ragas.llms import llm_factory

    load_dotenv()
    api_key = os.getenv("CEREBRAS_API_KEY")
    if not api_key:
        print("FAIL: CEREBRAS_API_KEY not found in .env")
        sys.exit(1)
    client = OpenAI(api_key=api_key, base_url=CEREBRAS_BASE_URL)
    # Pass model kwargs through llm_factory so every judge call uses the
    # raised token cap and deterministic temperature.
    return llm_factory(
        JUDGE_MODEL,
        provider="openai",
        client=client,
        max_tokens=JUDGE_MAX_TOKENS,
        temperature=JUDGE_TEMPERATURE,
    )


def build_run_config():
    from ragas.run_config import RunConfig
    return RunConfig(
        timeout=300,
        max_retries=10,
        max_wait=60,
        max_workers=1,
        log_tenacity=False,
    )


async def score_one(scorer, question, answer, contexts):
    from ragas.dataset_schema import SingleTurnSample

    if not contexts or not answer:
        return None
    sample = SingleTurnSample(
        user_input=question, response=answer, retrieved_contexts=contexts
    )
    for attempt in (1, 2):
        try:
            return float(await scorer.single_turn_ascore(sample))
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "rate" in msg.lower() or "RESOURCE_EXHAUSTED" in msg:
                if attempt == 1:
                    print(f"      rate limit; waiting {HARD_429_WAIT_S:.0f}s then retrying once...")
                    time.sleep(HARD_429_WAIT_S)
                    continue
                print("      rate limit again after wait; recording None.")
                return None
            print(f"      scoring error: {type(exc).__name__}: {msg[:120]}")
            return None
    return None


async def main_async(raw_path, out_path, label):
    from ragas.metrics import Faithfulness

    data = json.loads(raw_path.read_text(encoding="utf-8"))
    records = data["records"]

    llm = build_judge()
    run_config = build_run_config()
    faithfulness = Faithfulness(llm=llm)
    try:
        faithfulness.init(run_config)
    except Exception:
        pass

    print("=" * 60)
    print(f"Day 14 - RAGAS faithfulness scoring [{label}]")
    print(f"Judge: {JUDGE_MODEL} (Cerebras, max_tokens={JUDGE_MAX_TOKENS}) | samples: {len(records)}")
    print("=" * 60)

    scored = []
    for i, rec in enumerate(records, start=1):
        qid, lang = rec["question_id"], rec["language"]
        question = rec["question"]
        rag, baseline = rec["rag"], rec["baseline"]
        contexts = rag.get("contexts", [])

        print(f"[{i}/{len(records)}] {qid} ({lang}) grounded={rag.get('grounded')} ctx={len(contexts)}")

        rag_faith = await score_one(faithfulness, question, rag.get("answer"), contexts)
        if contexts:
            time.sleep(SLEEP_BETWEEN_SAMPLES_S)
        base_faith = await score_one(faithfulness, question, baseline.get("answer"), contexts)

        print(f"      rag_faithfulness={rag_faith}  baseline_faithfulness={base_faith}")

        scored.append({
            "question_id": qid,
            "language": lang,
            "answerable": rec["answerable"],
            "grounded": rag.get("grounded"),
            "num_contexts": len(contexts),
            "rag_faithfulness": rag_faith,
            "baseline_faithfulness": base_faith,
            "rag_latency_s": rag.get("latency_s"),
            "baseline_latency_s": baseline.get("latency_s"),
        })

        if contexts:
            time.sleep(SLEEP_BETWEEN_SAMPLES_S)

    output = {
        "label": label,
        "judge_model": JUDGE_MODEL,
        "judge_provider": "Cerebras",
        "source": str(raw_path),
        "scored": scored,
    }
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"Wrote {out_path} with {len(scored)} scored records.")


def main():
    if len(sys.argv) != 4:
        print("Usage: python eval/score_eval.py <raw.json> <out.json> <label>")
        sys.exit(1)
    raw_path, out_path, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    if not raw_path.exists():
        print(f"FAIL: {raw_path} not found.")
        sys.exit(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main_async(raw_path, out_path, label))


if __name__ == "__main__":
    main()
