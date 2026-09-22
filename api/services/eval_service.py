"""eval_service.py

Service layer for the evaluation dashboard.

Loads the RAGAS faithfulness scoring results produced during the Day 14
evaluation phase (eval/results/scored_030.json and scored_050.json) and
computes the three hypothesis metrics visualised by the dashboard:

    H1 - Grounding lifts answer faithfulness.
         Mean RAG vs. baseline faithfulness on answerable questions,
         at both retrieval thresholds, with the mean delta.

    H2 - Grounding does not increase latency.
         Median response latency, RAG vs. baseline.

    H3 - The retrieval threshold trades false groundings against
         non-English false negatives.
         At threshold 0.3, unanswerable questions are falsely grounded;
         at 0.5 that is fixed, but legitimate FR/PT questions fall below
         the similarity cut-off (an English-centric embedding bias in
         all-MiniLM-L6-v2), while English questions are unaffected.

This module is pure standard library: the statistical tests (paired
t-tests) were computed offline with SciPy during Day 14 and are stored
in eval/results; they are surfaced here as static annotations so that
SciPy is not required in the deployed application runtime.

The service is a thin computation layer. The route (api/routes/
eval_dashboard.py) only invokes it and serves the result; all metric
logic lives here, mirroring the logic committed in stats.py so the
dashboard and the thesis statistics can never diverge.
"""

from __future__ import annotations

import json
import statistics as st
from functools import lru_cache
from pathlib import Path
from typing import Any

from api.utils.config import get_settings
from api.utils.logger import logger


# Project root resolved from this file's location:
#   api/services/eval_service.py -> parents[2] == project root
# Resolving explicitly (rather than relying on the current working
# directory) keeps the service robust under Docker and Hugging Face
# Spaces, where the launch directory may differ from the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class EvalResultsNotFoundError(Exception):
    """Raised when a scored results file cannot be located on disk."""


class EvalService:
    """Computes evaluation hypothesis metrics from scored result files."""

    def __init__(self) -> None:
        settings = get_settings()
        self._results_dir = _PROJECT_ROOT / settings.eval_results_dir
        self._file_030 = self._results_dir / settings.eval_scored_030
        self._file_050 = self._results_dir / settings.eval_scored_050
        self._threshold = settings.retrieval_min_score

    # ── Internal helpers ───────────────────────────────────────────
    @staticmethod
    def _is_number(value: Any) -> bool:
        """True if value is a real numeric score (not None / missing)."""
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _load(self, path: Path) -> list[dict]:
        """Load the 'scored' record list from a results file.

        Raises:
            EvalResultsNotFoundError: if the file is absent, so the API
                can return a clean 404 instead of crashing.
        """
        if not path.exists():
            logger.error("Eval results file not found: %s", path)
            raise EvalResultsNotFoundError(str(path))
        with path.open(encoding="utf-8") as handle:
            return json.load(handle).get("scored", [])

    # ── H1: faithfulness lift ──────────────────────────────────────
    def _h1(self, rows: list[dict]) -> dict:
        pairs = [
            (r["rag_faithfulness"], r["baseline_faithfulness"])
            for r in rows
            if r.get("answerable")
            and self._is_number(r.get("rag_faithfulness"))
            and self._is_number(r.get("baseline_faithfulness"))
        ]
        rag = [a for a, _ in pairs]
        base = [b for _, b in pairs]
        return {
            "n_pairs": len(pairs),
            "mean_rag": round(st.mean(rag), 4) if rag else None,
            "mean_baseline": round(st.mean(base), 4) if base else None,
            "delta": round(st.mean(rag) - st.mean(base), 4) if pairs else None,
        }

    # ── H2: latency ────────────────────────────────────────────────
    def _h2(self, rows: list[dict]) -> dict:
        rag = [r["rag_latency_s"] for r in rows if self._is_number(r.get("rag_latency_s"))]
        base = [r["baseline_latency_s"] for r in rows if self._is_number(r.get("baseline_latency_s"))]
        return {
            "median_rag_s": round(st.median(rag), 3) if rag else None,
            "median_baseline_s": round(st.median(base), 3) if base else None,
            "n_rag": len(rag),
            "n_baseline": len(base),
        }

    # ── H3: threshold trade-off ────────────────────────────────────
    def _h3(self, rows: list[dict]) -> dict:
        false_groundings = [
            f"{r['question_id']}-{r['language']}"
            for r in rows
            if not r.get("answerable") and r.get("grounded")
        ]
        false_negatives = [
            r for r in rows if r.get("answerable") and not r.get("grounded")
        ]
        fn_en = [r for r in false_negatives if r["language"] == "en"]
        fn_frpt = [r for r in false_negatives if r["language"] in ("fr", "pt")]
        return {
            "false_groundings": false_groundings,
            "false_groundings_count": len(false_groundings),
            "false_negatives": [f"{r['question_id']}-{r['language']}" for r in false_negatives],
            "false_negatives_count": len(false_negatives),
            "false_negatives_en": len(fn_en),
            "false_negatives_frpt": len(fn_frpt),
        }

    # ── Public API ─────────────────────────────────────────────────
    def compute(self) -> dict:
        """Load both result files and return the full metric payload.

        Returns:
            A JSON-serialisable dict with H1, H2 and H3 metrics at both
            retrieval thresholds, plus the offline-computed paired t-test
            results and the currently configured threshold.
        """
        rows_030 = self._load(self._file_030)
        rows_050 = self._load(self._file_050)
        logger.info(
            "Eval metrics computed | 0.3: %d records, 0.5: %d records",
            len(rows_030), len(rows_050),
        )
        return {
            "configured_threshold": self._threshold,
            "h1": {
                "0.3": self._h1(rows_030),
                "0.5": self._h1(rows_050),
                # Paired t-tests computed offline (SciPy, Day 14):
                "ttest_030": {"t": 5.012, "p": 0.0002},
                "ttest_050": {"t": 4.036, "p": 0.0020},
            },
            "h2": {
                "0.3": self._h2(rows_030),
                "0.5": self._h2(rows_050),
            },
            "h3": {
                "0.3": self._h3(rows_030),
                "0.5": self._h3(rows_050),
            },
        }


@lru_cache()
def get_eval_service() -> EvalService:
    """Return a cached EvalService instance (FastAPI dependency).

    Mirrors get_rag_service / get_ollama_service: a single instance is
    reused per process, matching the application's dependency-injection
    convention.
    """
    return EvalService()
