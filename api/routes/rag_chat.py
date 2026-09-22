"""HTTP route for retrieval-augmented chat.

Exposes POST /chat/rag, the RAG-enabled counterpart to the baseline
POST /chat endpoint. Keeping them separate (rather than adding a flag
to /chat) preserves a clean baseline for the faithfulness comparison
in the project's evaluation phase: the same question can be sent to
both endpoints and their answers compared.

This module is a thin HTTP boundary. All retrieval and generation
logic lives in RAGService; the route only translates the request,
invokes the service, and maps failures to HTTP status codes.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from api.exceptions.document_exceptions import ChromaDBError, EmbeddingError
from api.models.document_schemas import RAGChatRequest, RAGChatResponse
from api.services.rag_service import RAGService, get_rag_service
from api.utils.logger import logger


router = APIRouter(tags=["Chat"])


@router.post(
    "/chat/rag",
    response_model=RAGChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask a question using retrieval-augmented generation",
    responses={
        200: {"description": "Answer generated. See `grounded` for whether retrieved context was used."},
        500: {"description": "Embedding, retrieval, or generation failure."},
    },
)
async def rag_chat(
    request: RAGChatRequest,
    rag_service: RAGService = Depends(get_rag_service),
) -> RAGChatResponse:
    """Answer a question using retrieval-augmented generation.

    The question is embedded, the vector store is searched for the most
    similar stored chunks, and an answer is generated. If at least one
    chunk clears the relevance threshold, the answer is grounded in that
    context and the chunks are returned as `sources`. If nothing is
    relevant enough, the model answers from its own knowledge and the
    response is flagged `grounded=False` with empty `sources`.

    Args:
        request: The question plus optional document scoping and top_k.

    Returns:
        A RAGChatResponse with the answer, grounding flag, sources, and
        the model identifier.

    Raises:
        HTTPException: 500 if embedding, retrieval, or generation fails.
    """
    try:
        return rag_service.answer(
            question=request.question,
            document_id=request.document_id,
            top_k=request.top_k,
        )
    except EmbeddingError as exc:
        logger.error("RAG embedding failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to embed the question. Please try again.",
        ) from exc
    except ChromaDBError as exc:
        logger.error("RAG retrieval failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve context from the vector store.",
        ) from exc
    except Exception as exc:
        # Generation failures (Ollama down, timeout) surface here. We
        # avoid leaking internal error text to the client; the detail
        # is logged for diagnosis.
        logger.error("RAG generation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate an answer. Is the language model running?",
        ) from exc
