"""Retrieval-Augmented Generation orchestration service.

Coordinates the full RAG flow for a user question:
    1. Embed the question into a query vector.
    2. Search the vector store for the top-k most similar chunks.
    3. Apply a relevance threshold to decide grounded vs ungrounded.
    4. Build a context-injected prompt (grounded) or pass the bare
       question through (ungrounded fallback).
    5. Generate an answer with the LLM.
    6. Assemble the response with sources and a grounding flag.

Design notes:
    - This service is the orchestrator; it owns no storage, no model
      weights, no HTTP concerns. It composes the embedding, vector
      store, and LLM services that already exist.
    - Grounding decision: a chunk counts as relevant only if its
      similarity score >= settings.retrieval_min_score. If no chunk
      clears the bar, the answer is generated from the LLM's own
      knowledge and flagged grounded=False (graceful fallback).
    - Prompt construction is the single biggest lever on answer
      quality and directly affects the H1 faithfulness hypothesis.
      The prompt explicitly instructs the model to answer FROM the
      context and to admit when the context is insufficient.
"""

from __future__ import annotations

from uuid import UUID

from api.models.document_schemas import RAGChatResponse, RetrievedChunk
from api.services.embedding_service import EmbeddingService, get_embedding_service
from api.services.llm_provider import LLMService, get_llm_service
from api.services.vector_store_service import (
    VectorStoreService,
    get_vector_store_service,
)
from api.utils.config import Settings, get_settings
from api.utils.logger import logger


# The grounded prompt template. The model is instructed to answer using
# only the provided context and to be honest about gaps. Numbered
# context blocks ([1], [2], ...) align with the source ordering in the
# response, so a reader can trace each claim back to a chunk.
_GROUNDED_PROMPT_TEMPLATE = """You are a helpful assistant answering questions based on provided context.

Use ONLY the context below to answer the question. If the context does not
contain enough information to answer, say so honestly rather than guessing.
Do not invent facts that are not in the context.

Context:
{context}

Question: {question}

Answer:"""


class RAGService:
    """Orchestrates retrieval-augmented generation over stored documents."""

    def __init__(
        self,
        settings: Settings,
        embedding_service: EmbeddingService,
        vector_store: VectorStoreService,
        llm_service: LLMService,
    ) -> None:
        """Initialize with the services this orchestrator composes.

        Args:
            settings: Application configuration. Reads retrieval_top_k
                and retrieval_min_score.
            embedding_service: For embedding the user question.
            vector_store: For similarity search over stored chunks.
            llm_service: For LLM generation. Any provider that
                satisfies the LLMService protocol (OllamaService
                locally, CerebrasService when hosted).
        """
        self._default_top_k: int = settings.retrieval_top_k
        self._min_score: float = settings.retrieval_min_score
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._llm_service = llm_service
        # Report the ACTIVE provider's model instead of a hardcoded
        # default: the name travels with the injected service, so
        # switching LLM_PROVIDER changes what responses report.
        self._model_name: str = llm_service.model_name

        logger.info(
            "RAGService ready: default_top_k=%d, min_score=%.2f, model=%s",
            self._default_top_k,
            self._min_score,
            self._model_name,
        )

    def answer(
        self,
        question: str,
        document_id: UUID | None = None,
        top_k: int | None = None,
    ) -> RAGChatResponse:
        """Answer a question using retrieval-augmented generation.

        Args:
            question: The user's question.
            document_id: Optional. Scope retrieval to a single document.
            top_k: Optional. Override the configured number of chunks
                to retrieve. Falls back to settings.retrieval_top_k.

        Returns:
            A RAGChatResponse containing the answer, a grounding flag,
            the source chunks used (empty if ungrounded), and the model
            identifier.
        """
        effective_top_k = top_k if top_k is not None else self._default_top_k

        # ---- 1. Embed the question ----
        query_embedding = self._embedding_service.embed_texts([question])[0]

        # ---- 2. Search the vector store ----
        candidates = self._vector_store.search(
            query_embedding=query_embedding,
            top_k=effective_top_k,
            document_id=document_id,
        )

        # ---- 3. Apply the relevance threshold ----
        relevant = [c for c in candidates if c.score >= self._min_score]

        if relevant:
            return self._answer_grounded(question, relevant)
        return self._answer_ungrounded(question, len(candidates))

    # ------------------------------------------------------------------ #
    # Grounded path
    # ------------------------------------------------------------------ #

    def _answer_grounded(
        self, question: str, sources: list[RetrievedChunk]
    ) -> RAGChatResponse:
        """Generate an answer using retrieved context.

        Args:
            question: The user's question.
            sources: Relevant chunks that cleared the threshold, ordered
                by similarity (highest first).

        Returns:
            A grounded RAGChatResponse.
        """
        context = "\n\n".join(
            f"[{i + 1}] {chunk.content}" for i, chunk in enumerate(sources)
        )
        prompt = _GROUNDED_PROMPT_TEMPLATE.format(
            context=context, question=question
        )

        logger.info(
            "Answering GROUNDED: %d sources, top_score=%.4f",
            len(sources),
            sources[0].score,
        )
        answer = self._llm_service.generate_answer(prompt)

        return RAGChatResponse(
            answer=answer,
            grounded=True,
            sources=sources,
            model=self._model_name,
        )

    # ------------------------------------------------------------------ #
    # Ungrounded fallback
    # ------------------------------------------------------------------ #

    def _answer_ungrounded(
        self, question: str, candidates_seen: int
    ) -> RAGChatResponse:
        """Generate an answer from the LLM's own knowledge.

        Triggered when no retrieved chunk cleared the relevance
        threshold (or the store was empty). The answer is flagged
        grounded=False so downstream evaluation can distinguish it
        from RAG-grounded answers.

        Args:
            question: The user's question.
            candidates_seen: How many chunks were returned by search
                before thresholding (for logging/diagnostics).

        Returns:
            An ungrounded RAGChatResponse with empty sources.
        """
        logger.info(
            "Answering UNGROUNDED: %d candidates seen, none >= min_score=%.2f",
            candidates_seen,
            self._min_score,
        )
        answer = self._llm_service.generate_answer(question)

        return RAGChatResponse(
            answer=answer,
            grounded=False,
            sources=[],
            model=self._model_name,
        )


# ---------------------------------------------------------------------- #
# Dependency-injection provider
# ---------------------------------------------------------------------- #

_rag_service_singleton: RAGService | None = None


def get_rag_service() -> RAGService:
    """Return the process-wide singleton RAGService.

    Lazily constructed on first call, composing the embedding, vector
    store, and active LLM provider singletons. Use via FastAPI's Depends()
    in route handlers.
    """
    global _rag_service_singleton
    if _rag_service_singleton is None:
        _rag_service_singleton = RAGService(
            settings=get_settings(),
            embedding_service=get_embedding_service(),
            vector_store=get_vector_store_service(),
            llm_service=get_llm_service(),
        )
    return _rag_service_singleton
