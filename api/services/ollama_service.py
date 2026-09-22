"""
ollama_service.py
─────────────────
Service module that encapsulates all interactions with the locally-running
Ollama runtime serving Microsoft Phi-3 Mini.

This module is the single point of communication between the FastAPI
application layer and the underlying Large Language Model. By isolating
Ollama-specific logic here, the rest of the codebase remains agnostic to
the choice of LLM runtime — allowing future substitution (e.g., switching
to llama.cpp or vLLM) without modifying the API endpoints.

Architecture pattern:
    Service layer (this file)  →  Ollama runtime (HTTP localhost:11434)
                              →  Phi-3 Mini (3.8B params, MIT license)

Author: Mohamed Yassine El Kesraoui
Project: RAG Chatbot Platform — ISLA Gaia Master's Thesis
"""

from __future__ import annotations

import logging
from typing import Optional

import ollama

from api.utils.config import Settings, get_settings
from api.utils.logger import setup_logger


# ────────────────────────────────────────────────────────────────────────
# Module-level logger
# ────────────────────────────────────────────────────────────────────────
logger: logging.Logger = setup_logger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Custom exceptions
# ────────────────────────────────────────────────────────────────────────
class OllamaConnectionError(Exception):
    """
    Raised when the Ollama runtime is unreachable.

    Typically occurs when the Ollama background service is not running
    or when network communication with localhost:11434 fails.
    """


class OllamaModelError(Exception):
    """
    Raised when Phi-3 Mini fails to generate a valid response.

    Typically occurs when the model is missing, corrupted, or when
    the prompt exceeds the model's context window.
    """


# ────────────────────────────────────────────────────────────────────────
# Service class
# ────────────────────────────────────────────────────────────────────────
class OllamaService:
    """
    Encapsulates all interactions with the Ollama runtime.

    This service is instantiated once at application startup and reused
    across all incoming requests, avoiding the overhead of re-establishing
    the connection on every call. It is registered with FastAPI's
    dependency-injection system via `get_ollama_service()`.

    Attributes:
        model_name: Identifier of the model to use (e.g., "phi3").
        temperature: Sampling temperature controlling response randomness.
        max_tokens: Maximum number of tokens to generate per response.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialize the service with configuration from environment variables.

        Args:
            settings: Application settings (loaded from .env via pydantic-settings).
        """
        self.model_name: str = settings.ollama_model
        self.temperature: float = settings.ollama_temperature
        self.max_tokens: int = settings.ollama_max_tokens

        logger.info(
            "OllamaService initialized | model=%s | temperature=%.2f | max_tokens=%d",
            self.model_name,
            self.temperature,
            self.max_tokens,
        )

    def health_check(self) -> bool:
        """
        Verify that the Ollama runtime is reachable and the configured model is loaded.

        This method is used both at startup (to fail fast on misconfiguration)
        and by the /health endpoint to expose runtime status to clients.

        Returns:
            True if Ollama is reachable AND the configured model is available;
            False otherwise.
        """
        try:
            # ollama.list() returns the list of locally-installed models
            response: dict = ollama.list()
            installed_models: list[str] = [
                model.get("name", "") for model in response.get("models", [])
            ]

            # Check whether our configured model is among the installed ones
            # Note: model names may appear as "phi3:latest" or "phi3"
            is_available: bool = any(
                self.model_name in name for name in installed_models
            )

            if is_available:
                logger.info("Health check OK | model '%s' is available", self.model_name)
            else:
                logger.warning(
                    "Health check FAILED | model '%s' not found in: %s",
                    self.model_name,
                    installed_models,
                )

            return is_available

        except Exception as exc:
            # If ollama.list() raises, the runtime is unreachable
            logger.error("Health check FAILED | cannot reach Ollama: %s", exc)
            return False

    def generate_answer(self, question: str) -> str:
        """
        Send a user question to Phi-3 Mini and return the generated answer.

        This is the main entry point used by the /chat endpoint. It wraps
        the Ollama Python client, applies the configured generation parameters,
        and translates any low-level errors into domain-specific exceptions.

        Args:
            question: The user's natural-language question (any language).

        Returns:
            The AI-generated answer as a string.

        Raises:
            OllamaConnectionError: If the Ollama runtime cannot be reached.
            OllamaModelError: If the model fails to produce a valid response.
        """
        # Log the incoming request (truncated to 80 chars for log readability)
        preview: str = question[:80] + "..." if len(question) > 80 else question
        logger.info("Generating answer | question_preview='%s'", preview)

        try:
            # Call Ollama's chat API
            # The `options` dict passes generation parameters to the model
            response: dict = ollama.chat(
                model=self.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": question,
                    }
                ],
                options={
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,  # Ollama's term for max_tokens
                },
            )

            # Extract the model's text response from the nested response structure
            ai_answer: Optional[str] = response.get("message", {}).get("content")

            if not ai_answer:
                logger.error("Model returned empty response | full_response=%s", response)
                raise OllamaModelError("Model returned an empty response.")

            # Log successful generation (just the length, not the content, for privacy)
            logger.info("Answer generated successfully | length=%d chars", len(ai_answer))

            return ai_answer.strip()

        except OllamaModelError:
            # Re-raise our own exceptions unchanged
            raise

        except ConnectionError as exc:
            # Network-level failure: Ollama service is not running
            logger.error("Ollama connection error: %s", exc)
            raise OllamaConnectionError(
                f"Cannot reach Ollama runtime at localhost:11434. "
                f"Is the Ollama service running? Original error: {exc}"
            ) from exc

        except Exception as exc:
            # Catch-all for any other low-level failure
            logger.error("Unexpected error during generation: %s", exc, exc_info=True)
            raise OllamaModelError(
                f"Failed to generate response from model '{self.model_name}'. "
                f"Original error: {exc}"
            ) from exc


# ────────────────────────────────────────────────────────────────────────
# Singleton instance + factory function for FastAPI dependency injection
# ────────────────────────────────────────────────────────────────────────
_service_instance: Optional[OllamaService] = None


def get_ollama_service() -> OllamaService:
    """
    Factory function that returns the singleton OllamaService instance.

    FastAPI's dependency-injection system calls this function whenever an
    endpoint depends on the Ollama service. By caching the instance in
    `_service_instance`, we avoid the overhead of recreating the service
    on every request.

    Returns:
        The singleton OllamaService instance.
    """
    global _service_instance

    if _service_instance is None:
        settings: Settings = get_settings()
        _service_instance = OllamaService(settings=settings)

    return _service_instance