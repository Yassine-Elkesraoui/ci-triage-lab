"""eval_dashboard.py

HTTP routes for the evaluation results dashboard.

Exposes two endpoints:

    GET /eval/data       JSON payload of the H1/H2/H3 hypothesis metrics,
                         computed live from the scored result files.

    GET /eval/dashboard  A self-contained HTML page that fetches /eval/data
                         and renders the three hypotheses as charts using
                         Chart.js (loaded from a CDN, so no additional
                         Python dependency enters the application runtime).

This module is a thin presentation boundary, consistent with the project's
four-tier architecture: all metric computation lives in EvalService; the
routes only invoke the service, serve its output, and map a missing-results
condition to an HTTP 404.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse

from api.services.eval_service import (
    EvalResultsNotFoundError,
    EvalService,
    get_eval_service,
)
from api.utils.logger import logger


router = APIRouter(tags=["Evaluation"])


@router.get(
    "/eval/data",
    summary="Hypothesis metrics (H1/H2/H3) as JSON",
    description=(
        "Returns the evaluation metrics computed from the Day 14 RAGAS "
        "scoring results at both retrieval thresholds (0.3 and 0.5): "
        "faithfulness lift (H1), median latency (H2), and the false "
        "grounding / false negative trade-off (H3)."
    ),
)
def eval_data(service: EvalService = Depends(get_eval_service)) -> JSONResponse:
    """Return the computed hypothesis metrics as JSON.

    Raises:
        HTTPException 404: if the scored result files are not present.
    """
    try:
        payload = service.compute()
    except EvalResultsNotFoundError as exc:
        logger.warning("Eval data requested but results missing: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Evaluation results not found. Run the scoring pipeline "
                "(eval/score_eval.py) to generate scored_030.json and "
                "scored_050.json."
            ),
        ) from exc
    return JSONResponse(content=payload)


@router.get(
    "/eval/dashboard",
    response_class=HTMLResponse,
    summary="Interactive evaluation dashboard",
    description=(
        "Serves an HTML page visualising the three project hypotheses as "
        "charts. The page fetches its data from /eval/data at load time."
    ),
)
def eval_dashboard() -> HTMLResponse:
    """Serve the self-contained dashboard HTML page."""
    logger.info("GET /eval/dashboard | serving dashboard page")
    return HTMLResponse(content=_DASHBOARD_HTML)


# ──────────────────────────────────────────────────────────────────────
# Static HTML for the dashboard.
# Kept as a module-level constant so the route stays a thin boundary.
# Chart.js is loaded from a CDN; no template engine or static-file mount
# is required, which keeps the deployment (Docker / Hugging Face Spaces)
# simple and dependency-light.
# ──────────────────────────────────────────────────────────────────────
_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RAG Evaluation Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root { --bg:#0f1117; --card:#1a1d27; --ink:#e6e8ee; --muted:#9aa0b0;
            --rag:#4f8cff; --base:#ff7a59; --line:#2a2e3c; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink);
           font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
    header { padding:28px 32px 8px; }
    h1 { margin:0 0 4px; font-size:22px; }
    .sub { color:var(--muted); font-size:14px; }
    .grid { display:grid; grid-template-columns:1fr 1fr; gap:20px;
            padding:20px 32px 40px; max-width:1200px; }
    .card { background:var(--card); border:1px solid var(--line);
            border-radius:14px; padding:20px; }
    .card.wide { grid-column:1 / -1; }
    .card h2 { margin:0 0 2px; font-size:16px; }
    .card .hint { color:var(--muted); font-size:13px; margin:0 0 14px; }
    .pill { display:inline-block; padding:2px 10px; border-radius:999px;
            background:#222637; color:var(--muted); font-size:12px;
            margin-left:8px; }
    .err { color:#ff7a7a; padding:20px 32px; }
    canvas { max-height:300px; }
    .stat { font-size:13px; color:var(--muted); margin-top:10px; }
    .stat b { color:var(--ink); }
  </style>
</head>
<body>
  <header>
    <h1>RAG Evaluation Dashboard <span class="pill" id="threshold-pill">threshold —</span></h1>
    <div class="sub">Trilingual faithfulness evaluation (EN / FR / PT) &middot; Master's Thesis, ISLA Gaia</div>
  </header>
  <div id="error" class="err" style="display:none"></div>
  <div class="grid">
    <div class="card">
      <h2>H1 &middot; Faithfulness lift</h2>
      <p class="hint">Mean RAG vs. baseline faithfulness on answerable questions.</p>
      <canvas id="h1"></canvas>
      <div class="stat" id="h1-stat"></div>
    </div>
    <div class="card">
      <h2>H2 &middot; Median latency (s)</h2>
      <p class="hint">Lower is faster. RAG vs. baseline response time.</p>
      <canvas id="h2"></canvas>
      <div class="stat" id="h2-stat"></div>
    </div>
    <div class="card wide">
      <h2>H3 &middot; Threshold trade-off</h2>
      <p class="hint">False groundings (unanswerable yet answered) vs. false negatives (answerable yet rejected), by threshold and language.</p>
      <canvas id="h3"></canvas>
      <div class="stat" id="h3-stat"></div>
    </div>
  </div>

  <script>
    const RAG = "#4f8cff", BASE = "#ff7a59", GRID = "#2a2e3c", INK = "#e6e8ee";
    Chart.defaults.color = "#9aa0b0";
    Chart.defaults.borderColor = GRID;

    async function load() {
      let data;
      try {
        const res = await fetch("/eval/data");
        if (!res.ok) throw new Error("HTTP " + res.status);
        data = await res.json();
      } catch (e) {
        document.getElementById("error").style.display = "block";
        document.getElementById("error").textContent =
          "Could not load evaluation data: " + e.message;
        return;
      }

      document.getElementById("threshold-pill").textContent =
        "configured threshold " + data.configured_threshold;

      // H1 — grouped bars, RAG vs baseline, at each threshold
      new Chart(document.getElementById("h1"), {
        type: "bar",
        data: {
          labels: ["threshold 0.3", "threshold 0.5"],
          datasets: [
            { label: "RAG", backgroundColor: RAG,
              data: [data.h1["0.3"].mean_rag, data.h1["0.5"].mean_rag] },
            { label: "Baseline", backgroundColor: BASE,
              data: [data.h1["0.3"].mean_baseline, data.h1["0.5"].mean_baseline] },
          ],
        },
        options: { scales: { y: { beginAtZero:true, max:1,
          title:{display:true,text:"mean faithfulness"} } } },
      });
      document.getElementById("h1-stat").innerHTML =
        "&Delta; @0.3 = <b>+" + data.h1["0.3"].delta + "</b> (t=" + data.h1.ttest_030.t +
        ", p=" + data.h1.ttest_030.p + ") &nbsp;&middot;&nbsp; &Delta; @0.5 = <b>+" +
        data.h1["0.5"].delta + "</b> (t=" + data.h1.ttest_050.t + ", p=" + data.h1.ttest_050.p + ")";

      // H2 — median latency, RAG vs baseline
      new Chart(document.getElementById("h2"), {
        type: "bar",
        data: {
          labels: ["threshold 0.3", "threshold 0.5"],
          datasets: [
            { label: "RAG", backgroundColor: RAG,
              data: [data.h2["0.3"].median_rag_s, data.h2["0.5"].median_rag_s] },
            { label: "Baseline", backgroundColor: BASE,
              data: [data.h2["0.3"].median_baseline_s, data.h2["0.5"].median_baseline_s] },
          ],
        },
        options: { scales: { y: { beginAtZero:true,
          title:{display:true,text:"median seconds"} } } },
      });
      document.getElementById("h2-stat").innerHTML =
        "RAG is faster: grounding shortens generation rather than adding overhead.";

      // H3 — false groundings vs false negatives at each threshold
      new Chart(document.getElementById("h3"), {
        type: "bar",
        data: {
          labels: ["threshold 0.3", "threshold 0.5"],
          datasets: [
            { label: "False groundings (unanswerable answered)", backgroundColor: "#e0b341",
              data: [data.h3["0.3"].false_groundings_count, data.h3["0.5"].false_groundings_count] },
            { label: "False negatives EN", backgroundColor: "#7a8cff",
              data: [data.h3["0.3"].false_negatives_en, data.h3["0.5"].false_negatives_en] },
            { label: "False negatives FR/PT", backgroundColor: "#ff5e7a",
              data: [data.h3["0.3"].false_negatives_frpt, data.h3["0.5"].false_negatives_frpt] },
          ],
        },
        options: { scales: { y: { beginAtZero:true, ticks:{stepSize:1},
          title:{display:true,text:"count"} } } },
      });
      const fn = data.h3["0.5"].false_negatives.join(", ");
      document.getElementById("h3-stat").innerHTML =
        "At 0.5, all " + data.h3["0.5"].false_negatives_count +
        " false negatives are FR/PT (" + fn + "), none English &mdash; " +
        "consistent with English-centric bias in all-MiniLM-L6-v2.";
    }
    load();
  </script>
</body>
</html>"""
