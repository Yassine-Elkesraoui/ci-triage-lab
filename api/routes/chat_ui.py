"""chat_ui.py

HTTP route serving the browser chat frontend.

Exposes one endpoint:

    GET /chat/ui    A self-contained HTML page that lets a user ask a
                    question and see the answer, its grounding status,
                    the retrieved sources with their similarity scores,
                    and the model that generated it.

The page talks to the API same-origin via fetch, calling POST /chat/rag
by default and POST /chat when the baseline toggle is on. Because both
calls are relative, the page works unchanged on localhost and on the
Hugging Face Space with no configuration.

The main column shows only the latest exchange. Every exchange, that one
included, also appears in a slide-out history panel the moment its answer
arrives, as one collapsed line -- ordinal, time, mode and the question --
that expands in place to show the answer it produced. The two are
separate renderings of the same response, not one node moved between
them. That history lives only in the DOM: nothing is written to
localStorage or sessionStorage, and -- importantly -- earlier exchanges
are never sent back to the API. Each request carries one
question and nothing else, because the system holds no conversational
state. The page says so explicitly next to the input, so neither the
panel nor the stacked layout can be mistaken for a chat thread with
memory.

Unlike /eval/dashboard, this page loads NOTHING from a CDN or from any
other URL: all CSS and JS are inline, and the ISLA logo is embedded as a
base64 data URI at import time. The rendered page therefore issues zero
subresource requests, and no static-file route or mount is needed.

This module is a thin presentation boundary, consistent with the
project's four-tier architecture: it holds no chat logic at all. The
browser calls the existing endpoints directly; this route only serves
the document.
"""

import base64
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from api.utils.logger import logger


router = APIRouter(tags=["Chat"])


@router.get(
    "/chat/ui",
    response_class=HTMLResponse,
    summary="Browser chat interface",
    description=(
        "Serves a self-contained HTML page for asking questions "
        "interactively. The page calls POST /chat/rag (retrieval-"
        "augmented) or POST /chat (baseline) same-origin and renders "
        "the answer, its grounded/ungrounded status, the retrieved "
        "sources with similarity scores, and the reported model. "
        "Every exchange also appears in a session-only history panel."
    ),
)
def chat_ui() -> HTMLResponse:
    """Serve the self-contained chat frontend."""
    logger.info("GET /chat/ui | serving chat page")
    return HTMLResponse(content=_CHAT_HTML)


# ──────────────────────────────────────────────────────────────────────
# Logo embedding.
#
# The institutional logo is inlined as a base64 data URI rather than
# served from a static mount, so the page stays a single self-contained
# document with no subresource requests -- the same reasoning that keeps
# the CSS and JS inline. The path is resolved from __file__ rather than
# the working directory, because uvicorn may be started from anywhere.
#
# A missing file is a presentation problem, not a fatal one: it logs a
# warning and the header renders without the logo instead of raising at
# import time and taking the whole application down with it.
# ──────────────────────────────────────────────────────────────────────
_LOGO_PATH = Path(__file__).resolve().parents[1] / "static" / "isla-logo.png"


def _logo_img_tag() -> str:
    """Return the header <img> for the logo, or "" if it cannot be read."""
    try:
        encoded = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning("chat_ui | logo not embedded (%s): %s", _LOGO_PATH, exc)
        return ""
    return (
        '<img class="logo" width="666" height="300" '
        'alt="ISLA - Instituto Politecnico de Gestao e Tecnologia" '
        'src="data:image/png;base64,' + encoded + '">'
    )


# ──────────────────────────────────────────────────────────────────────
# Static HTML for the chat frontend.
# Kept as a module-level constant so the route stays a thin boundary,
# matching the approach in eval_dashboard.py: no template engine and no
# static-file mount, which keeps the Docker / Hugging Face Spaces
# deployment simple and dependency-light.
#
# NOTE: this is a plain (non-f) string, and the only substitution applied
# to it is a literal str.replace() of the __LOGO_IMG__ placeholder below.
# It is never passed through .format(), so the braces in the inline CSS
# and JS need no escaping.
# ──────────────────────────────────────────────────────────────────────
_CHAT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RAG Chatbot</title>
  <style>
    /* Colour language is unchanged from the first version of this page:
       blue (--rag) for retrieval-augmented, coral (--base) for the
       baseline arm, green / amber for grounded / ungrounded. --sunk and
       --faint are new *neutrals* only, added so nested surfaces and
       tertiary text get their own step instead of --bg and --muted each
       doing two different jobs. */
    :root { --bg:#0f1117; --card:#1a1d27; --sunk:#13161f;
            --ink:#e6e8ee; --muted:#9aa0b0; --faint:#6b7185;
            --rag:#4f8cff; --base:#ff7a59; --line:#2a2e3c;
            --ok:#3fb950; --warn:#e0b341; --err:#ff7a7a;
            --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px;
            --r-sm:8px; --r-md:12px; --r-lg:16px;
            --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink);
           font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
           font-size:15px; -webkit-font-smoothing:antialiased; }

    /* ── header ──────────────────────────────────────────────── */
    header { padding:var(--s6) var(--s6) var(--s5);
             border-bottom:1px solid var(--line); }
    .inner { max-width:920px; margin:0 auto; }
    .title-row { display:flex; align-items:center; gap:var(--s5);
                 flex-wrap:wrap; margin-bottom:var(--s3); }
    h1 { margin:0; font-size:26px; font-weight:600; letter-spacing:-.02em; }
    /* The ISLA lockup is black artwork on transparency, which would be
       invisible on this dark header. It sits on a white plaque so the
       institution's own colours are reproduced exactly, rather than
       being inverted into colours the institution does not use. */
    .logo { height:48px; width:auto; display:block; background:#fff;
            border-radius:var(--r-sm); padding:6px 10px; }
    .sub { margin:0; color:var(--muted); font-size:14px; line-height:1.6; }
    .sub a { color:var(--rag); text-decoration:none; }
    .sub a:hover { text-decoration:underline; }

    .wrap { max-width:920px; margin:0 auto;
            padding:var(--s5) var(--s6) var(--s6); }
    .card { background:var(--card); border:1px solid var(--line);
            border-radius:var(--r-lg); padding:var(--s5); }

    /* ── mode toggle ─────────────────────────────────────────── */
    .modes { display:flex; gap:var(--s2); margin-bottom:var(--s3); }
    .mode { flex:0 0 auto; padding:7px 16px; border-radius:999px;
            border:1px solid var(--line); background:#222637;
            color:var(--muted); font-size:13px; font-weight:500;
            cursor:pointer; font-family:inherit; }
    .mode[aria-pressed="true"] { color:#fff; border-color:transparent; }
    #mode-rag[aria-pressed="true"] { background:var(--rag); }
    #mode-base[aria-pressed="true"] { background:var(--base); }
    .mode-hint { color:var(--muted); font-size:13px; line-height:1.55;
                 margin:0 0 var(--s4); }

    /* ── input ───────────────────────────────────────────────── */
    .composer { display:flex; align-items:flex-start; gap:var(--s3); }
    .hist-btn { flex:0 0 auto; width:40px; height:40px; display:flex;
                align-items:center; justify-content:center; cursor:pointer;
                background:#222637; color:var(--muted); font-family:inherit;
                border:1px solid var(--line); border-radius:var(--r-md); }
    .hist-btn:hover { color:var(--ink); border-color:var(--rag); }
    .hist-btn[aria-expanded="true"] { color:#fff; background:var(--rag);
                                      border-color:transparent; }
    textarea { flex:1 1 auto; width:100%; min-width:0; min-height:96px;
               resize:none;
               background:var(--bg); color:var(--ink);
               border:1px solid var(--line); border-radius:var(--r-md);
               padding:14px; font-size:15px; font-family:inherit;
               line-height:1.6; }
    textarea:focus { outline:none; border-color:var(--rag); }
    .row { display:flex; align-items:center; gap:var(--s3);
           margin-top:var(--s3); flex-wrap:wrap; }
    button.ask { padding:10px 24px; border:none; border-radius:var(--r-md);
                 background:var(--rag); color:#fff; font-size:14px;
                 font-weight:600; cursor:pointer; font-family:inherit; }
    button.ask:hover:not(:disabled) { filter:brightness(1.08); }
    button.ask:disabled { opacity:.5; cursor:not-allowed; }
    .keys { display:flex; align-items:center; gap:6px; flex-wrap:wrap;
            color:var(--faint); font-size:12px; }
    .kbd { font-family:var(--mono); font-size:11px; color:var(--faint);
           border:1px solid var(--line); border-radius:6px; padding:3px 7px; }
    .counter { color:var(--faint); font-size:12px; margin-left:auto;
               font-variant-numeric:tabular-nums; }
    .stateless { margin:var(--s3) 0 0; padding-top:var(--s3);
                 border-top:1px solid var(--line); color:var(--faint);
                 font-size:12.5px; line-height:1.6; }

    /* ── loading ─────────────────────────────────────────────── */
    .loading { display:none; align-items:center; gap:var(--s3);
               color:var(--muted); font-size:13.5px; }
    .loading.on { display:flex; }
    .spinner { width:16px; height:16px; flex:0 0 16px; border-radius:50%;
               border:2px solid var(--line); border-top-color:var(--rag);
               animation:spin .8s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }
    .elapsed { font-family:var(--mono); font-size:12.5px;
               font-variant-numeric:tabular-nums; color:var(--ink); }

    /* ── error ───────────────────────────────────────────────── */
    #error { display:none; margin-top:var(--s5); color:var(--err);
             font-size:14px; line-height:1.6;
             border:1px solid rgba(255,122,122,.35);
             background:rgba(255,122,122,.08); }

    /* ── exchange ────────────────────────────────────────────── */
    #latest { margin-top:var(--s5); }
    .entry { background:var(--card); border:1px solid var(--line);
             border-radius:var(--r-lg); padding:var(--s5); }
    .entry-head { display:flex; align-items:center; gap:var(--s2);
                  margin-bottom:var(--s4); }
    .entry-head::after { content:""; flex:1 1 auto; height:1px;
                         background:var(--line); margin-left:var(--s2); }
    .ord { font-family:var(--mono); font-size:12px; font-weight:600;
           color:var(--faint); }
    .time { font-size:12px; color:var(--faint);
            font-variant-numeric:tabular-nums; }

    /* Question: accent rule in the mode's colour, smaller and dimmer
       than the answer, with its own label. Three cues separate it from
       the answer below -- colour, size and indent -- so the two do not
       blur together when the page is read from a distance. */
    .q { border-left:2px solid var(--line); padding-left:var(--s4);
         margin-bottom:var(--s5); }
    .entry.rag .q { border-left-color:var(--rag); }
    .entry.baseline .q { border-left-color:var(--base); }
    .q-label { display:block; font-size:11px; text-transform:uppercase;
               letter-spacing:.09em; color:var(--faint);
               margin-bottom:var(--s1); }
    .q-text { margin:0; font-size:14px; line-height:1.55;
              color:var(--muted); white-space:pre-wrap; max-width:68ch; }

    .badges { display:flex; gap:var(--s2); align-items:center;
              flex-wrap:wrap; margin-bottom:var(--s4); }
    .badge { display:inline-block; padding:4px 12px; border-radius:999px;
             font-size:11.5px; font-weight:600; letter-spacing:.04em; }
    .badge.grounded { background:rgba(63,185,80,.15); color:var(--ok);
                      border:1px solid rgba(63,185,80,.35); }
    .badge.ungrounded { background:rgba(224,179,65,.15); color:var(--warn);
                        border:1px solid rgba(224,179,65,.35); }
    .badge.baseline { background:rgba(255,122,89,.15); color:var(--base);
                      border:1px solid rgba(255,122,89,.35); }
    .badge.meta { background:#222637; color:var(--muted);
                  border:1px solid var(--line); font-weight:400;
                  letter-spacing:0; }
    .answer { white-space:pre-wrap; line-height:1.7; font-size:15px;
              color:var(--ink); max-width:68ch; }
    .answer strong { font-weight:650; color:#fff; }
    .answer em { font-style:italic; }
    .answer code { font-family:var(--mono); font-size:13px;
                   background:var(--sunk); border:1px solid var(--line);
                   border-radius:5px; padding:1px 5px; }
    .note { color:var(--faint); font-size:12.5px; line-height:1.6;
            margin:var(--s4) 0 0; max-width:68ch; }

    /* ── sources ─────────────────────────────────────────────── */
    .sources { margin-top:var(--s5); padding-top:var(--s5);
               border-top:1px solid var(--line); }
    h2 { margin:0 0 var(--s1); font-size:12px; font-weight:600;
         text-transform:uppercase; letter-spacing:.09em; color:var(--muted); }
    .hint { color:var(--faint); font-size:12.5px; line-height:1.55;
            margin:0 0 var(--s4); }
    .src { background:var(--sunk); border:1px solid var(--line);
           border-radius:var(--r-md); padding:var(--s4);
           margin-bottom:var(--s3); }
    .src:last-child { margin-bottom:0; }
    .src-head { display:flex; align-items:center; gap:var(--s3);
                margin-bottom:var(--s3); flex-wrap:wrap; }
    .src-rank { flex:0 0 auto; width:22px; height:22px;
                border-radius:var(--r-sm); background:var(--rag);
                color:#fff; font-family:var(--mono); font-size:11.5px;
                font-weight:700; display:flex; align-items:center;
                justify-content:center; }
    .src-id { font-family:var(--mono); font-size:11.5px;
              color:var(--muted); word-break:break-all; }
    .src-index { flex:0 0 auto; font-size:11px; color:var(--faint);
                 border:1px solid var(--line); border-radius:999px;
                 padding:2px 8px; }
    .src-score { margin-left:auto; font-size:11px; color:var(--faint);
                 letter-spacing:.04em; text-transform:uppercase; }
    .src-score span { margin-left:var(--s2); font-family:var(--mono);
                      font-size:12.5px; font-weight:600; color:var(--ink);
                      font-variant-numeric:tabular-nums; letter-spacing:0;
                      text-transform:none; }
    /* Cosine similarity drawn on a plain 0-1 track. No threshold marker
       is drawn: this page cannot read retrieval_min_score, so any marker
       would be a hardcoded constant that would contradict the server's
       actual grounding decision whenever the threshold differs. */
    .bar { height:6px; background:var(--line); border-radius:999px;
           overflow:hidden; margin-bottom:var(--s3); }
    .bar i { display:block; height:100%; border-radius:999px;
             background:linear-gradient(90deg,rgba(79,140,255,.5),var(--rag)); }
    .src-body { white-space:pre-wrap; font-size:12.5px; line-height:1.65;
                color:var(--muted); max-height:180px; overflow-y:auto; }
    .src-body::-webkit-scrollbar { width:8px; }
    .src-body::-webkit-scrollbar-thumb { background:var(--line);
                                         border-radius:999px; }

    /* ── history panel ───────────────────────────────────────── */
    .backdrop { position:fixed; inset:0; z-index:10;
                background:rgba(8,10,15,.6); }
    .panel { position:fixed; top:0; left:0; bottom:0; z-index:20;
             width:min(440px,88vw); background:var(--card);
             border-right:1px solid var(--line); padding:var(--s5);
             overflow-y:auto; transform:translateX(-100%);
             visibility:hidden;
             transition:transform .22s ease, visibility 0s linear .22s; }
    .panel.open { transform:translateX(0); visibility:visible;
                  transition:transform .22s ease, visibility 0s linear 0s; }
    .panel:focus { outline:none; }
    .panel-title { font-size:13px; margin:0 0 var(--s3);
                   padding-bottom:var(--s3);
                   border-bottom:1px solid var(--line); }
    .panel-empty { color:var(--faint); font-size:13px; margin:0; }
    #hist-list { display:flex; flex-direction:column; }
    #hist-list:empty { display:none; }

    /* One exchange per row, collapsed. The row is a real <button>, so it
       takes focus and responds to Enter and Space on its own, with no
       keyboard handling of ours to get wrong. */
    .hist-item { border-bottom:1px solid var(--line); }
    .hist-line { display:flex; align-items:center; gap:var(--s2);
                 width:100%; text-align:left; cursor:pointer;
                 background:none; border:none; font-family:inherit;
                 color:var(--ink); padding:var(--s3) var(--s1); }
    .hist-line:hover { background:var(--sunk); }
    .hist-line:focus-visible { outline:2px solid var(--rag);
                               outline-offset:-2px; }
    .hist-ord { flex:0 0 auto; font-family:var(--mono); font-size:11.5px;
                font-weight:600; color:var(--faint); }
    .hist-time { flex:0 0 auto; font-size:11.5px; color:var(--faint);
                 font-variant-numeric:tabular-nums; }
    .hist-mode { flex:0 0 auto; font-size:10px; font-weight:700;
                 letter-spacing:.06em; border-radius:999px; padding:2px 7px; }
    .hist-mode.rag { background:rgba(79,140,255,.15); color:var(--rag);
                     border:1px solid rgba(79,140,255,.35); }
    .hist-mode.baseline { background:rgba(255,122,89,.15); color:var(--base);
                          border:1px solid rgba(255,122,89,.35); }
    /* Truncation is CSS, not a JS slice: the line stays exactly one line
       at any panel width, and no character budget has to be guessed. */
    .hist-q { flex:1 1 auto; min-width:0; font-size:13px;
              white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
              color:var(--muted); }
    .hist-line[aria-expanded="true"] .hist-q { color:var(--ink); }
    .hist-detail { padding:0 var(--s1) var(--s4); }

    /* An expanded row holds its own copy of the exchange, built from the
       same response as the main column's. Its head and question are
       hidden because the collapsed line already shows them, and its card
       chrome is dropped so the expansion reads as part of the row rather
       than as a second card floating inside the panel. That leaves the
       entry on the panel's own --card, so nested source cards step down
       to --bg to keep the nesting visible. */
    .panel .entry { background:none; border:none; border-radius:0;
                    padding:0; }
    .panel .entry-head, .panel .q { display:none; }
    .panel .answer { font-size:14px; }
    .panel .src { background:var(--bg); }
    .panel .src-body { max-height:120px; }

    /* ── footer ──────────────────────────────────────────────── */
    footer { border-top:1px solid var(--line);
             padding:var(--s5) var(--s6) var(--s6); }
    footer p { margin:0; color:var(--faint); font-size:12.5px;
               line-height:1.75; }
    footer a { color:var(--rag); text-decoration:none; }
    footer a:hover { text-decoration:underline; }

    @media (max-width:640px) {
      header { padding:var(--s5) var(--s4) var(--s4); }
      .wrap { padding:var(--s4); }
      footer { padding:var(--s4); }
      h1 { font-size:22px; }
      .title-row { gap:var(--s4); }
      .logo { height:38px; }
      .card, .entry { padding:var(--s4); }
      .counter { margin-left:0; }
    }
    @media (prefers-reduced-motion:reduce) {
      .panel { transition:none; }
      .spinner { animation:none; }
    }
  </style>
</head>
<body>
  <header>
    <div class="inner">
      <div class="title-row">
        <h1>RAG Chatbot</h1>
        __LOGO_IMG__
      </div>
      <p class="sub">
        Trilingual retrieval-augmented chat (EN / FR / PT) &middot;
        Master's Thesis, ISLA Gaia &middot;
        <a href="/eval/dashboard">evaluation dashboard</a>
      </p>
    </div>
  </header>

  <div class="wrap">
    <div class="card">
      <div class="modes">
        <button type="button" class="mode" id="mode-rag" aria-pressed="true">RAG</button>
        <button type="button" class="mode" id="mode-base" aria-pressed="false">Baseline</button>
      </div>
      <p class="mode-hint" id="mode-hint"></p>

      <div class="composer">
        <button type="button" class="hist-btn" id="hist-btn" aria-expanded="false"
                aria-controls="history" aria-label="Session history"
                title="Session history">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
               stroke="currentColor" stroke-width="2" stroke-linecap="round"
               stroke-linejoin="round" aria-hidden="true" focusable="false">
            <path d="M3 3v6h6"></path>
            <path d="M3.51 15a9 9 0 1 0 2.13-9.36L3 9"></path>
            <path d="M12 7v5l4 2"></path>
          </svg>
        </button>
        <textarea id="q" placeholder="Ask a question about the indexed corpus..."
                  maxlength="2000" autofocus></textarea>
      </div>
      <div class="row">
        <button type="button" class="ask" id="ask">Ask</button>
        <span class="keys">
          <span class="kbd">Enter</span> to send
          <span class="kbd">Shift + Enter</span> for a new line
        </span>
        <span class="counter" id="counter">0 / 2000</span>
      </div>
      <div class="row loading" id="loading">
        <span class="spinner"></span>
        <span id="loading-text"></span>
        <span class="elapsed" id="elapsed">0s</span>
      </div>
      <p class="stateless">
        Each question is answered independently, no conversational memory.
      </p>
    </div>

    <div class="card" id="error"></div>

    <div id="latest"></div>
  </div>

  <footer>
    <div class="inner">
      <p>Mohamed Yassine El Kesraoui (Student number: 22509786)</p>
      <p>ISLA Gaia, Polytechnic Institute of Management and Technology,
         Vila Nova de Gaia, Porto, Portugal</p>
      <p><a href="mailto:yassine.elkesraoui@gmail.com">yassine.elkesraoui@gmail.com</a></p>
      <p>Advisor: Professor Firmino da Silva</p>
    </div>
  </footer>

  <div class="backdrop" id="backdrop" hidden></div>
  <aside class="panel" id="history" role="dialog" aria-modal="true"
         aria-label="Session history" tabindex="-1">
    <h2 class="panel-title">Session history</h2>
    <div id="hist-list"></div>
    <p class="panel-empty" id="hist-empty">No previous exchanges yet.</p>
  </aside>

  <script>
    // Same-origin endpoints. Relative paths keep the page working
    // unchanged on localhost and on the Hugging Face Space.
    var RAG_URL = "/chat/rag";
    var BASE_URL = "/chat";

    // Generation on local Ollama can take ~45s; allow generous headroom
    // before giving up so a slow-but-alive request is not killed.
    var TIMEOUT_MS = 120000;

    // POST /chat enforces min_length=3 on the question and POST /chat/rag
    // enforces min_length=1. We apply the stricter bound in both modes so
    // toggling to baseline can never produce a surprise 422.
    var MIN_CHARS = 3;

    var mode = "rag";
    var busy = false;
    var timer = null;

    // Exchange ordinal. The history is just the DOM under #hist-list:
    // nothing is persisted, and nothing is replayed to the API.
    var seq = 0;

    var els = {
      q: document.getElementById("q"),
      ask: document.getElementById("ask"),
      counter: document.getElementById("counter"),
      modeRag: document.getElementById("mode-rag"),
      modeBase: document.getElementById("mode-base"),
      modeHint: document.getElementById("mode-hint"),
      loading: document.getElementById("loading"),
      loadingText: document.getElementById("loading-text"),
      elapsed: document.getElementById("elapsed"),
      latest: document.getElementById("latest"),
      error: document.getElementById("error"),
      histBtn: document.getElementById("hist-btn"),
      histList: document.getElementById("hist-list"),
      histEmpty: document.getElementById("hist-empty"),
      panel: document.getElementById("history"),
      backdrop: document.getElementById("backdrop")
    };

    // ── mode ──────────────────────────────────────────────────
    function setMode(next) {
      if (busy) return;
      mode = next;
      var isRag = mode === "rag";
      els.modeRag.setAttribute("aria-pressed", isRag ? "true" : "false");
      els.modeBase.setAttribute("aria-pressed", isRag ? "false" : "true");
      els.ask.style.background = isRag ? "var(--rag)" : "var(--base)";
      els.modeHint.textContent = isRag
        ? "POST /chat/rag - retrieves context from the vector store, then answers from it."
        : "POST /chat - baseline: the same question goes straight to the model, with no retrieval.";
    }
    els.modeRag.addEventListener("click", function () { setMode("rag"); });
    els.modeBase.addEventListener("click", function () { setMode("baseline"); });

    // ── history panel ─────────────────────────────────────────
    function setHistory(open) {
      els.panel.className = open ? "panel open" : "panel";
      els.backdrop.hidden = !open;
      els.histBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) els.panel.focus();
      else els.histBtn.focus();
    }

    function historyOpen() {
      return els.histBtn.getAttribute("aria-expanded") === "true";
    }

    els.histBtn.addEventListener("click", function () {
      setHistory(!historyOpen());
    });
    els.backdrop.addEventListener("click", function () { setHistory(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && historyOpen()) setHistory(false);
    });

    // ── helpers ───────────────────────────────────────────────
    // textContent everywhere, never innerHTML: answers are model output
    // and chunk bodies are user-uploaded document text, so neither may
    // ever be interpreted as markup.
    function el(tag, cls, text) {
      var n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text !== undefined && text !== null) n.textContent = text;
      return n;
    }

    function badge(cls, text) {
      return el("span", "badge " + cls, text);
    }

    function showError(message) {
      els.error.textContent = message;
      els.error.style.display = "block";
    }

    // Minimal inline-markdown renderer for model answers, which arrive
    // containing **bold**, *italic* and `code`.
    //
    // Every piece of the string reaches the DOM either as a text node or
    // as the textContent of an element that this function created, so
    // innerHTML is never involved and markup in model output cannot
    // become markup on the page. The security property is identical to
    // assigning textContent directly.
    //
    // Scope is deliberately inline-only: no links, headings, tables or
    // lists, and an unmatched marker stays visible as a literal
    // character. Underscore emphasis (_x_) is NOT supported on purpose,
    // because answers in this project routinely contain snake_case
    // identifiers such as retrieval_min_score, which underscore rules
    // would silently mangle into italics.
    function renderMarkdown(target, text) {
      var re = /\\*\\*(?=\\S)([\\s\\S]*?\\S)\\*\\*|\\*(?=\\S)([\\s\\S]*?\\S)\\*|`([^`]+)`/g;
      var last = 0;
      var m;
      while ((m = re.exec(text)) !== null) {
        if (m.index > last) {
          target.appendChild(document.createTextNode(text.slice(last, m.index)));
        }
        if (m[1] !== undefined) target.appendChild(el("strong", null, m[1]));
        else if (m[2] !== undefined) target.appendChild(el("em", null, m[2]));
        else target.appendChild(el("code", null, m[3]));
        last = re.lastIndex;
      }
      if (last < text.length) {
        target.appendChild(document.createTextNode(text.slice(last)));
      }
    }

    function setBusy(on) {
      busy = on;
      els.ask.disabled = on;
      els.q.disabled = on;
      els.loading.className = on ? "row loading on" : "row loading";
      if (on) {
        els.loadingText.textContent = mode === "rag"
          ? "Embedding question, searching the store, generating..."
          : "Generating without retrieval...";
        var started = Date.now();
        els.elapsed.textContent = "0s";
        timer = setInterval(function () {
          els.elapsed.textContent =
            Math.round((Date.now() - started) / 1000) + "s";
        }, 250);
      } else if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    els.q.addEventListener("input", function () {
      els.counter.textContent = els.q.value.length + " / 2000";
    });

    // Enter sends; Shift+Enter inserts a newline. The isComposing guard
    // matters for a trilingual interface: without it, confirming an IME
    // candidate with Enter would fire the request mid-word.
    els.q.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        ask();
      }
    });
    els.ask.addEventListener("click", ask);

    // ── rendering ─────────────────────────────────────────────
    function buildSources(sources) {
      var section = el("section", "sources");
      section.appendChild(el("h2", null, "Sources"));
      section.appendChild(el("p", "hint",
        "Chunks retrieved above the relevance threshold, ordered by similarity."));

      sources.forEach(function (s, i) {
        var card = el("div", "src");

        var head = el("div", "src-head");
        head.appendChild(el("span", "src-rank", String(i + 1)));
        head.appendChild(el("span", "src-id", "chunk " + s.chunk_id));
        head.appendChild(el("span", "src-index", "index " + s.index));

        var score = el("span", "src-score", "similarity");
        score.appendChild(el("span", null, Number(s.score).toFixed(4)));
        head.appendChild(score);
        card.appendChild(head);

        // Score bar. Cosine similarity is in [0, 1] for the normalized
        // embeddings this project uses, so the score maps to width directly.
        var bar = el("div", "bar");
        var fill = el("i");
        var pct = Math.max(0, Math.min(1, Number(s.score))) * 100;
        fill.style.width = pct + "%";
        bar.appendChild(fill);
        card.appendChild(bar);

        // Chunk bodies are raw corpus text and are NOT markdown-rendered:
        // an asterisk there is a literal character from the document.
        card.appendChild(el("div", "src-body", s.content));
        section.appendChild(card);
      });

      return section;
    }

    // Builds the common frame of one exchange: ordinal, time and the
    // question, plus the empty answer nodes for the caller to fill.
    //
    // Everything identifying the exchange arrives on `meta`, minted once
    // per exchange in ask(). Nothing here reads the clock or bumps the
    // counter, so this can be called twice for the SAME exchange -- once
    // for the main column, once for its history row -- and both trees
    // carry the same ordinal and the same timestamp.
    function buildShell(meta) {
      var entry = el("article", "entry " + meta.mode);

      var head = el("div", "entry-head");
      head.appendChild(el("span", "ord", "#" + meta.seq));
      head.appendChild(el("span", "time", meta.time));
      entry.appendChild(head);

      var q = el("div", "q");
      q.appendChild(el("span", "q-label", "Question"));
      q.appendChild(el("p", "q-text", meta.question));
      entry.appendChild(q);

      var badges = el("div", "badges");
      var answer = el("div", "answer");
      var note = el("p", "note");
      entry.appendChild(badges);
      entry.appendChild(answer);
      entry.appendChild(note);

      return { entry: entry, badges: badges, answer: answer, note: note };
    }

    function buildRagEntry(meta, data) {
      var s = buildShell(meta);
      var sources = data.sources || [];

      if (data.grounded) {
        s.badges.appendChild(badge("grounded",
          "GROUNDED - " + sources.length +
          (sources.length === 1 ? " source" : " sources")));
      } else {
        s.badges.appendChild(badge("ungrounded",
          "UNGROUNDED - answered from model knowledge"));
      }
      s.badges.appendChild(badge("meta", "model: " + data.model));
      // Client-side, not an API field: RAGChatResponse carries no status.
      // buildRagEntry() is only reached after res.ok, so reaching here
      // means the request returned 200.
      s.badges.appendChild(badge("meta", "status: success"));

      renderMarkdown(s.answer, data.answer);
      s.note.textContent = data.grounded
        ? "Answer generated from the retrieved context below."
        : "No stored chunk cleared the relevance threshold, so the model " +
          "answered from its own knowledge. This is the documented fallback, " +
          "not an error.";

      if (sources.length) s.entry.appendChild(buildSources(sources));
      return s.entry;
    }

    function buildBaselineEntry(meta, data) {
      var s = buildShell(meta);
      s.badges.appendChild(badge("baseline", "BASELINE - no retrieval"));
      // POST /chat returns ChatResponse (question, answer, status) and
      // carries no model field: its contract is frozen as the comparison
      // arm of the thesis evaluation. Both endpoints resolve their LLM
      // through get_llm_service(), so the provider is the one /chat/rag
      // names -- we can say that much without inventing a field.
      s.badges.appendChild(badge("meta", "model: same provider as RAG"));
      if (data.status) {
        s.badges.appendChild(badge("meta", "status: " + data.status));
      }
      renderMarkdown(s.answer, data.answer);
      s.note.textContent =
        "Baseline answer: the question went straight to the model with no " +
        "retrieval, so there are no sources and no grounding flag.";
      return s.entry;
    }

    // One exchange rendered from its response. Called once for the main
    // column, and again -- same meta, same data -- the first time this
    // exchange's history row is expanded.
    function buildEntry(meta, data) {
      return meta.mode === "rag"
        ? buildRagEntry(meta, data)
        : buildBaselineEntry(meta, data);
    }

    // ── history rows ──────────────────────────────────────────
    // At most one row is expanded at a time; this is that row, or null.
    var openRow = null;

    function toggleRow(line, detail) {
      var wasOpen = line.getAttribute("aria-expanded") === "true";
      if (openRow && openRow.line !== line) {
        openRow.line.setAttribute("aria-expanded", "false");
        openRow.detail.hidden = true;
      }
      line.setAttribute("aria-expanded", wasOpen ? "false" : "true");
      detail.hidden = wasOpen;
      openRow = wasOpen ? null : { line: line, detail: detail };
    }

    // One collapsed row for one exchange: a summary line and the detail
    // region it controls. The row appears as soon as the answer does,
    // while the same exchange is still on show in the main column, so the
    // two must be independent trees -- moving a node here would empty the
    // main column behind the user's back.
    //
    // The detail copy is therefore BUILT, not moved, and built lazily: a
    // row that is never expanded costs only its line. detail.firstChild
    // is the "already built" flag, so collapse and re-expand are free.
    function historyRow(meta, data) {
      var row = el("div", "hist-item");

      var detail = el("div", "hist-detail");
      detail.id = "hist-detail-" + meta.seq;
      detail.hidden = true;

      var line = el("button", "hist-line");
      line.type = "button";
      line.setAttribute("aria-expanded", "false");
      line.setAttribute("aria-controls", detail.id);
      line.appendChild(el("span", "hist-ord", "#" + meta.seq));
      line.appendChild(el("span", "hist-time", meta.time));
      line.appendChild(el("span", "hist-mode " + meta.mode,
        meta.mode === "rag" ? "RAG" : "BASE"));
      line.appendChild(el("span", "hist-q", meta.question));
      // A <button> fires click for Enter and Space by itself, so this one
      // listener is the whole keyboard story.
      line.addEventListener("click", function () {
        if (!detail.firstChild) detail.appendChild(buildEntry(meta, data));
        toggleRow(line, detail);
      });

      row.appendChild(line);
      row.appendChild(detail);
      return row;
    }

    // Show one finished exchange in both places at once: the main column,
    // which holds the newest one only, and a fresh collapsed row at the
    // top of the history panel, newest first. The entry the main column
    // was holding is discarded rather than moved -- its own history row
    // already exists further down the list, and rebuilds itself from its
    // stored response if it is ever expanded.
    function showEntry(meta, data) {
      var previous = els.latest.firstChild;
      if (previous) els.latest.removeChild(previous);
      els.latest.appendChild(buildEntry(meta, data));

      els.histList.insertBefore(historyRow(meta, data),
                                els.histList.firstChild);
      els.histEmpty.hidden = els.histList.childElementCount > 0;
    }

    // ── request ───────────────────────────────────────────────
    async function ask() {
      if (busy) return;
      var question = els.q.value.trim();

      els.error.style.display = "none";
      if (question.length < MIN_CHARS) {
        showError("Please enter a question of at least " + MIN_CHARS +
                  " characters.");
        return;
      }

      setBusy(true);
      var controller = new AbortController();
      var abortTimer = setTimeout(function () { controller.abort(); },
                                  TIMEOUT_MS);

      try {
        // One question per request. No earlier exchange is attached: the
        // API is stateless, and the history panel is presentation only.
        var res = await fetch(mode === "rag" ? RAG_URL : BASE_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: question }),
          signal: controller.signal
        });

        var data = null;
        try { data = await res.json(); } catch (e) { data = null; }

        if (!res.ok) {
          var detail = data && data.detail ? data.detail : "HTTP " + res.status;
          if (typeof detail !== "string") detail = JSON.stringify(detail);
          showError(detail);
          return;
        }

        // Ordinal and timestamp are minted once, here, and shared by
        // every rendering of this exchange.
        seq += 1;
        showEntry({
          seq: seq,
          time: new Date().toLocaleTimeString(
            [], { hour: "2-digit", minute: "2-digit" }),
          mode: mode,
          question: question
        }, data);
      } catch (e) {
        if (e.name === "AbortError") {
          showError("The request timed out after " + (TIMEOUT_MS / 1000) +
                    "s. Local generation is slow; the model may still be " +
                    "loading. Try again.");
        } else {
          showError("Request failed: " + e.message);
        }
      } finally {
        clearTimeout(abortTimer);
        setBusy(false);
      }
    }

    setMode("rag");
  </script>
</body>
</html>"""

# Substitute the embedded logo. A literal str.replace(), not .format():
# the CSS and JS braces in the template above stay untouched.
_CHAT_HTML = _CHAT_HTML_TEMPLATE.replace("__LOGO_IMG__", _logo_img_tag())
