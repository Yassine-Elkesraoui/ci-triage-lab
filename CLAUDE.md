# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

A Master's thesis project: an open-source **multilingual RAG chatbot platform**.
Author: Mohamed Yassine El Kesraoui — ISLA Polytechnic Institute of Management and
Technology, Vila Nova de Gaia, Portugal.

Because this is thesis work, correctness and reproducibility matter more than
velocity, and the evaluation data is evidence rather than a build artifact.

## Rules

- **NEVER modify anything under `eval/results/`** — those are banked evaluation
  results backing the thesis. Reading them is fine; writing to them is not.
- **NEVER re-run the evaluation scripts** (`eval/run_eval.py`, `eval/score_eval.py`,
  `eval/ingest_corpus.py`).
- **Read files to verify structure before writing code.** Never assume the layout —
  see "Gotchas" below; several things are not where they look.
- **Give honest critique, not validation.** Say when something is a bad idea, and
  say why.
- **Prefer small, reviewable changes** over large rewrites. No opportunistic
  refactoring beyond what the task requires.
- **Do not commit or push unless explicitly asked.** Leave finished work in the
  working tree and report what changed.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI (`api/main.py`, uvicorn) |
| Vector store | ChromaDB, persisted at `data/chroma` |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-dim, CPU, cached in `models/`) |
| LLM (local) | Phi-3 via Ollama — `LLM_PROVIDER=ollama` (default) |
| LLM (cloud) | Cerebras `gpt-oss-120b` — `LLM_PROVIDER=cerebras` |
| Deployment | Single Docker image on Hugging Face Spaces, listening on `0.0.0.0:7860` |

The application code is provider-agnostic; `api/services/llm_provider.py` is the
only place that decides between `ollama_service` and `cerebras_service`. Ollama is
**not** in the Docker image — the cloud deployment sets `LLM_PROVIDER=cerebras` and
supplies `CEREBRAS_API_KEY` as a Space secret. No secret is ever baked into the image.

## Layout

```
api/
  main.py            FastAPI app: lifespan corpus seeding, router registration, / and /health
  routes/            chat.py, rag_chat.py, documents.py, eval_dashboard.py
  services/          llm_provider, ollama_service, cerebras_service, rag_service,
                     embedding_service, vector_store_service, chunking_service,
                     parsing_service, document_service, eval_service, seed_service
  models/            schemas.py (chat), document_schemas.py (documents + RAG)
  exceptions/        document_exceptions.py (ChromaDBError, EmbeddingError, ...)
  utils/             config.py (pydantic-settings), logger.py
eval/                corpus/ (en, fr, pt), test_questions.json, run_eval.py,
                     score_eval.py, ingest_corpus.py, results/  ← read-only
data/                chroma/, uploads/, temp/  (runtime, gitignored)
models/              baked HuggingFace embedding-model cache
tests/               test_app.py
```

### Endpoints

| Method | Path | Module |
|---|---|---|
| POST | `/chat` | `routes/chat.py` — plain LLM chat, no retrieval |
| POST | `/chat/rag` | `routes/rag_chat.py` — retrieval-augmented chat |
| POST | `/documents/` | `routes/documents.py` — upload |
| POST | `/documents/{document_id}/process` | parse → chunk → embed → store |
| GET | `/documents/{document_id}/chunks` | list stored chunks |
| GET | `/eval/data` | `routes/eval_dashboard.py` — scored results as JSON |
| GET | `/eval/dashboard` | HTML dashboard |
| GET | `/`, `/health` | `api/main.py` |

## Configuration

All configuration goes through `api/utils/config.py` (`Settings` via
pydantic-settings, read from `.env`, exposed by the `lru_cache`d `get_settings()`).
Add new configuration there rather than reading `os.environ` directly, and never
hardcode a value that belongs in the environment — the project follows 12-Factor
config separation deliberately, and that choice is argued in the thesis.

Values worth knowing:

- `retrieval_min_score` — **0.5 in practice** (the config default of 0.3 is
  overridden by `.env` and by the Space variable). This is the threshold the whole
  evaluation is built on. The banked results carry the distinction in their
  filenames: `scored_030.json` / `raw_results_threshold030.json` are the 0.3 run,
  `scored_050.json` is the 0.5 run.
- `retrieval_top_k=4`, `chunk_size=1000`, `chunk_overlap=200`,
  `embedding_dimension=384`, `corpus_dir=eval/corpus`,
  `chroma_collection_name=document_chunks`.

More generally: defaults declared in `config.py` are frequently superseded by `.env`
and by Space variables. Check the environment before quoting a default as the
effective value.

## Running

```bash
pip install -r requirements.txt          # requirements-eval.txt for the eval harness
uvicorn api.main:app --reload            # local dev, :8000 per api_port
pytest tests/                            # app constructs, routes registered, / and /health
docker build -t rag-chatbot . && docker run -p 7860:7860 rag-chatbot
```

Local generation needs Ollama running with the `phi3` model pulled. On startup the
lifespan hook calls `seed_corpus_if_empty()`, which ingests `eval/corpus/` into
ChromaDB when the collection is empty — self-healing for the ephemeral HF Spaces
filesystem.

## Gotchas

- `src/` exists but is empty (`__init__.py` only). All real code is under `api/`.
- Several stale `.bak` files sit next to live modules (`api/routes/chat.py.bak`,
  `api/services/cerebras_service.py.bak`, `eval/score_eval.py.bak`). They are dead
  weight — never edit one and never treat it as the source of truth.
- Assorted root-level probe scripts (`probe_groq.py`, `probe_cerebras.py`,
  `probe_quota.py`, `list_models.py`, `stats.py`, `test_gemini_key.py`,
  `check_scored.py`) are ad-hoc throwaways, not part of the application.
  `test_gemini_key.py` is not a pytest test despite the name.
- The Dockerfile copies all of `eval/` into the image so the dashboard can serve
  the scored results. Adding large files under `eval/` inflates the image.
