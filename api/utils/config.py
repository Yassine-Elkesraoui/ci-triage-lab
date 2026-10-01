"""config.py
Centralized application configuration using pydantic-settings.

All configuration values are loaded from environment variables (or .env file)
following the 12-Factor App methodology (Wiggins, 2017), which mandates strict
separation between code and configuration.

This module exposes a single `Settings` class consumed by the rest of the
application via the `get_settings()` factory function.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Pydantic automatically reads values from the .env file at module import
    time, validates their types, and exposes them as typed attributes.
    """

    # ── Application metadata ───────────────────────────────────────
    app_name: str = "RAG Chatbot Platform"
    app_version: str = "1.0.0"
    app_env: str = "development"

    # ── API server configuration ───────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"

    # ── Ollama / Phi-3 Mini configuration ──────────────────────────
    ollama_model: str = "phi3"
    # ── LLM provider selection ─────────────────────────────────────
    # "ollama" = local Phi-3 (private, on-host, used for evaluation).
    # "cerebras" = hosted gpt-oss-120b via OpenAI-compatible API
    # (fast, used for the public cloud demo).
    # "groq" = hosted openai/gpt-oss-120b via OpenAI-compatible API
    # (second hosted option, same model family as the Cerebras arm).
    # The application code is provider-agnostic; only this switch
    # changes which LLM is called.
    llm_provider: str = "ollama"
    cerebras_model: str = "gpt-oss-120b"
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    cerebras_max_tokens: int = 1024
    cerebras_temperature: float = 0.7
    cerebras_api_key: str = ""  # loaded from .env / Space secret; never hardcoded
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_max_tokens: int = 1024
    groq_temperature: float = 0.7
    groq_api_key: str = ""  # loaded from .env / Space secret; never hardcoded
    ollama_temperature: float = 0.7
    ollama_max_tokens: int = 512

    # ── Document upload configuration ──────────────────────────────
    upload_dir: str = "data/uploads"

    # ── Corpus seeding configuration ───────────────────────────────
    # Directory of corpus files baked into the image. On startup, if
    # the vector store is empty, these are ingested automatically
    # (self-healing for ephemeral deployments like HF Spaces).
    corpus_dir: str = "eval/corpus"

    # ── Document chunking configuration ────────────────────────────
    chunk_size: int = 1000
    chunk_overlap: int = 2000

    # ── Embedding configuration ────────────────────────────────────
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_batch_size: int = 32
    embedding_dimension: int = 384
    embedding_normalize: bool = True
    embedding_device: str = "cpu"
    model_cache_dir: str = "models"

    # ── Vector store (ChromaDB) configuration ──────────────────────
    chroma_persist_dir: str = "data/chroma"
    chroma_collection_name: str = "document_chunks"

    # ── Retrieval configuration ────────────────────────────────────
    retrieval_top_k: int = 4
    retrieval_min_score: float = 0.3
    # ── Evaluation results configuration ───────────────────────────
    eval_results_dir: str = "eval/results"
    eval_scored_030: str = "scored_030.json"
    eval_scored_050: str = "scored_050.json"

    @field_validator("app_env", mode="after")
    @classmethod
    def _strip_app_env(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v
    # ── Pydantic configuration ─────────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Factory function returning a cached Settings instance.

    The `@lru_cache` decorator ensures Settings is instantiated only once
    per process, avoiding repeated .env parsing on every API request.

    Returns:
        The singleton Settings instance.
    """
    return Settings()
