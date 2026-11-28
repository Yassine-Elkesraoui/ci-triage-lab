# ─────────────────────────────────────────────────────────────────────
# RAG Chatbot Platform — application image
#
# Contains: FastAPI app, embedding model (all-MiniLM-L6-v2, baked in),
# ChromaDB, and the LLM provider abstraction. Does NOT contain Ollama:
# generation is delegated to a provider chosen at runtime via the
# LLM_PROVIDER env var. For the public cloud deployment that is
# "cerebras" (hosted gpt-oss-120b); locally it can be "ollama".
#
# Listens on 0.0.0.0:7860 for Hugging Face Spaces compatibility.
# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Avoid interactive prompts; keep Python output unbuffered for live logs.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Minimal system build tools (some wheels compile native extensions).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer; only re-runs when
# requirements.txt changes), before copying source for fast rebuilds.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Bake the embedding model into an image layer so the container starts
# instantly with no runtime download. Uses the SAME model name and cache
# folder as EmbeddingService, so the app finds it locally at startup.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer(model_name_or_path='sentence-transformers/all-MiniLM-L6-v2', cache_folder='models')"

# Copy application code and the two scored eval files (for /eval/dashboard).
COPY api/ ./api/
COPY eval/ ./eval/

# Create runtime data directories the app expects (excluded from context).
RUN mkdir -p data/chroma data/uploads data/temp

# Default provider is "ollama" (from config); the deployment overrides
# LLM_PROVIDER=cerebras and supplies CEREBRAS_API_KEY at runtime, so no
# secret is ever baked into the image.
EXPOSE 7860

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
