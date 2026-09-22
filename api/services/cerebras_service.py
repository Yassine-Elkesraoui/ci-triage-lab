"""
cerebras_service.py
───────────────────
Service module that encapsulates interactions with the Cerebras hosted
inference API, serving the gpt-oss-120b model through an OpenAI-compatible
endpoint.

This is a cloud-mode counterpart to OllamaService. All providers
implement the same informal interface — generate_answer(question) and
health_check() — so the rest of the application (routes, RAGService) is
agnostic to which LLM provider is active. Provider selection happens in
a single factory (get_llm_service) driven by the `llm_provider` setting.

Design rationale:
    - OllamaService (local Phi-3): private, on-host generation. Used for
      the evaluation phase and the data-sovereign local deployment.
    - CerebrasService (hosted gpt-oss-120b): fast, publicly accessible
      generation. Used for the live cloud demo, where sub-second latency
      matters more than on-host data residency.
    - GroqService (hosted openai/gpt-oss-120b): an alternative hosted
      route to the same model family, kept as a fallback vendor.

The API key is supplied as the CEREBRAS_API_KEY environment variable and
reaches this module through `Settings.cerebras_api_key` (pydantic-settings
reads .env and the process environment); it is never hardcoded in source,
consistent with 12-Factor config.

Author: Mohamed Yassine El Kesraoui
Project: RAG Chatbot Platform — ISLA Gaia Master's Thesis
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from openai import OpenAI, RateLimitError

from api.utils.config import Settings, get_settings
from api.utils.logger import setup_logger


logger: logging.Logger = setup_logger(__name__)


class CerebrasConnectionError(Exception):
    """Raised when the Cerebras API is unreachable or the key is invalid."""


class CerebrasModelError(Exception):
    """Raised when the model fails to return a usable response."""


class CerebrasService:
    """Encapsulates all interactions with the Cerebras inference API.

    Implements the same interface as OllamaService and GroqService so the
    three are interchangeable behind the get_llm_service factory.

    Attributes:
        model_name: Hosted model identifier (e.g. "gpt-oss-120b").
            Public, because RAGService reports the model actually used.
        temperature: Sampling temperature.
        max_tokens: Maximum completion tokens. Kept generous because
            gpt-oss-120b is a reasoning model that consumes tokens on
            internal reasoning before emitting the visible answer.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the service from application settings + environment.

        Args:
            settings: Application settings (pydantic-settings).

        Raises:
            CerebrasConnectionError: if CEREBRAS_API_KEY is not set.
        """
        api_key: Optional[str] = settings.cerebras_api_key
        if not api_key:
            logger.error("CEREBRAS_API_KEY not found in environment")
            raise CerebrasConnectionError(
                "CEREBRAS_API_KEY is not set. Add it to the environment "
                "(.env locally, or a Space secret in production)."
            )

        self.model_name: str = settings.cerebras_model
        self.temperature: float = settings.cerebras_temperature
        self.max_tokens: int = settings.cerebras_max_tokens
        self._client = OpenAI(
            base_url=settings.cerebras_base_url,
            api_key=api_key,
        )

        logger.info(
            "CerebrasService initialized | model=%s | temperature=%.2f | max_tokens=%d",
            self.model_name,
            self.temperature,
            self.max_tokens,
        )

    def health_check(self) -> bool:
        """Verify the API is reachable and the configured model resolves.

        Performs a minimal one-token completion. Returns True on success,
        False on any failure, so it can back a /health endpoint without
        raising.
        """
        try:
            self._client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            logger.info("Health check OK | Cerebras model '%s' reachable", self.model_name)
            return True
        except Exception as exc:
            logger.error("Health check FAILED | cannot reach Cerebras: %s", exc)
            return False

    def generate_answer(self, question: str) -> str:
        """Send a prompt to the hosted model and return the answer.

        Mirrors OllamaService.generate_answer so the two are drop-in
        interchangeable.

        Args:
            question: The prompt (raw question, or a RAG-assembled prompt).

        Returns:
            The generated answer text.

        Raises:
            CerebrasConnectionError: on network/auth failure.
            CerebrasModelError: if the model returns no usable content.
        """
        preview: str = question[:80] + "..." if len(question) > 80 else question
        logger.info("Generating answer (Cerebras) | question_preview='%s'", preview)

        # The Cerebras free tier intermittently returns HTTP 429
        # (code 'queue_exceeded') when its shared queue is saturated.
        # This is transient server-side congestion, not a client error,
        # so we retry with a short backoff before giving up. A persistent
        # 429 (or any other failure) still surfaces as a clean error.
        max_attempts: int = 3
        backoff_s: float = 2.0

        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": question}],
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                ai_answer: Optional[str] = response.choices[0].message.content

                if not ai_answer or not ai_answer.strip():
                    logger.error("Cerebras returned empty content | response=%s", response)
                    raise CerebrasModelError("Model returned an empty response.")

                logger.info("Answer generated successfully | length=%d chars", len(ai_answer))
                return ai_answer.strip()

            except CerebrasModelError:
                raise
            except RateLimitError as exc:
                if attempt < max_attempts:
                    wait = backoff_s * attempt
                    logger.warning(
                        "Cerebras queue busy (429), attempt %d/%d; retrying in %.1fs",
                        attempt, max_attempts, wait,
                    )
                    time.sleep(wait)
                    continue
                logger.error("Cerebras still rate-limited after %d attempts", max_attempts)
                raise CerebrasConnectionError(
                    "Cerebras is busy (queue_exceeded) and did not free up "
                    f"after {max_attempts} attempts. Please try again shortly."
                ) from exc
            except Exception as exc:
                logger.error("Unexpected error during Cerebras generation: %s", exc, exc_info=True)
                raise CerebrasConnectionError(
                    f"Failed to generate response from Cerebras model "
                    f"'{self.model_name}'. Original error: {exc}"
                ) from exc


_service_instance: Optional[CerebrasService] = None


def get_cerebras_service() -> CerebrasService:
    """Return the singleton CerebrasService instance (lazy construction)."""
    global _service_instance
    if _service_instance is None:
        _service_instance = CerebrasService(settings=get_settings())
    return _service_instance
