"""
chat.py
───────
Chat endpoint route handler.

Exposes POST /chat, which forwards user questions to the configured
Large Language Model (Phi-3 Mini via Ollama) and returns the generated
answer.

This module sits in the Routes layer of the four-tier architecture:

    Routes (this file)
        ↓
    Services (OllamaService)
        ↓
    Ollama runtime + Phi-3 Mini

The endpoint is intentionally thin: it validates the request via Pydantic,
delegates the actual AI work to OllamaService, translates service-level
exceptions into appropriate HTTP responses, and returns the result.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from api.models.schemas import ChatRequest, ChatResponse
from api.services.ollama_service import (
    OllamaConnectionError,
    OllamaModelError,
    OllamaService,
    get_ollama_service,
)
from api.services.llm_provider import get_llm_service
from api.utils.logger import setup_logger


# ────────────────────────────────────────────────────────────────────────
# Module-level logger
# ────────────────────────────────────────────────────────────────────────
logger = setup_logger(__name__)


# ────────────────────────────────────────────────────────────────────────
# Router declaration
# ────────────────────────────────────────────────────────────────────────
router = APIRouter(tags=["Chat"])


# ────────────────────────────────────────────────────────────────────────
# POST /chat — process a user question
# ────────────────────────────────────────────────────────────────────────
@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send a question to the AI and receive a generated answer",
    description=(
        "Forwards the user's question to Microsoft Phi-3 Mini via the "
        "Ollama runtime, running locally on the deployment host. "
        "No data is transmitted outside the host, ensuring full "
        "data sovereignty in compliance with GDPR."
    ),
)
def chat(
    request: ChatRequest,
    ollama: OllamaService = Depends(get_llm_service),
) -> ChatResponse:
    """
    Process a chat request by delegating generation to the Ollama service.

    Args:
        request: Validated request body containing the user's question.
        ollama: OllamaService instance injected by FastAPI's DI system.

    Returns:
        A ChatResponse containing the AI-generated answer.

    Raises:
        HTTPException 503: If the Ollama runtime is unreachable.
        HTTPException 500: If the model fails to generate a valid response.
    """
    logger.info("POST /chat | received question (length=%d)", len(request.question))

    try:
        # Delegate the actual AI generation to the service layer.
        # The service raises domain-specific exceptions on failure,
        # which we translate to HTTP responses below.
        ai_answer: str = ollama.generate_answer(request.question)

        return ChatResponse(
    question=request.question,
    answer=ai_answer,
    status="success",
)

    except OllamaConnectionError as exc:
        # The Ollama background service is not reachable.
        # This is a *temporary* infrastructure problem → 503 Service Unavailable.
        logger.error("Chat failed: Ollama unreachable | %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The AI service is currently unavailable. "
                "Please verify that the Ollama runtime is running and try again."
            ),
        ) from exc

    except OllamaModelError as exc:
        # The model itself failed (empty response, context overflow, etc.).
        # This is an *internal* problem → 500 Internal Server Error.
        logger.error("Chat failed: model error | %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The AI model failed to generate a response. "
                "Please try rephrasing your question or contact support."
            ),
        ) from exc