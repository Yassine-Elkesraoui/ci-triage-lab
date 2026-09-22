"""
llm_provider.py
───────────────
Provider-selection factory for the application's language model.

The application supports three interchangeable LLM backends:

    - OllamaService   (local Phi-3 Mini): private, on-host generation,
                      used for the evaluation phase and the data-sovereign
                      local deployment.
    - CerebrasService (hosted gpt-oss-120b): fast, publicly accessible
                      generation, used for the live cloud demo.
    - GroqService     (hosted openai/gpt-oss-120b): a second hosted route
                      to the same model family, so the cloud demo does not
                      depend on a single vendor's availability or quota.

All three expose the same informal interface:

    generate_answer(question: str) -> str
    health_check() -> bool
    model_name: str   (attribute naming the model actually in use)

Because the three are structurally interchangeable (duck typing), the rest
of the application depends only on this interface, never on a concrete
provider. Which backend is active is decided solely by the `llm_provider`
setting ("ollama", "cerebras" or "groq"), realising the runtime-substitution
design anticipated in OllamaService's module docstring.

This is the Strategy pattern: the calling code (routes, RAGService) is
fixed; the generation strategy is swapped via configuration without any
change to the callers.

Author: Mohamed Yassine El Kesraoui
Project: RAG Chatbot Platform — ISLA Gaia Master's Thesis
"""

from __future__ import annotations

from typing import Protocol

from api.utils.config import get_settings
from api.utils.logger import setup_logger


logger = setup_logger(__name__)


class LLMService(Protocol):
    """Structural interface that every LLM provider must satisfy.

    Declared as a typing.Protocol so existing services conform without
    needing to inherit from it — they already implement these methods.
    """

    model_name: str
    """Identifier of the model this provider actually generates with."""

    def generate_answer(self, question: str) -> str: ...
    def health_check(self) -> bool: ...


def get_llm_service() -> LLMService:
    """Return the active LLM service based on the `llm_provider` setting.

    Returns:
        An OllamaService, CerebrasService or GroqService singleton,
        depending on configuration. All satisfy the LLMService interface,
        so callers can use the returned object without knowing which
        provider it is.

    Raises:
        ValueError: if `llm_provider` is not a recognised value.
    """
    provider = get_settings().llm_provider.lower().strip()

    if provider == "ollama":
        from api.services.ollama_service import get_ollama_service
        logger.info("LLM provider selected: ollama (local Phi-3)")
        return get_ollama_service()

    if provider == "cerebras":
        from api.services.cerebras_service import get_cerebras_service
        logger.info("LLM provider selected: cerebras (hosted gpt-oss-120b)")
        return get_cerebras_service()

    if provider == "groq":
        from api.services.groq_service import get_groq_service
        logger.info("LLM provider selected: groq (hosted openai/gpt-oss-120b)")
        return get_groq_service()

    raise ValueError(
        f"Unknown llm_provider '{provider}'. "
        f"Expected 'ollama', 'cerebras' or 'groq'."
    )
