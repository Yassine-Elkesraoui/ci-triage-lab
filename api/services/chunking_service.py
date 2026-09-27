"""Document chunking service.

Splits extracted text into overlapping chunks suitable for embedding
and retrieval. Wraps LangChain's RecursiveCharacterTextSplitter with
project-specific defaults and metadata attachment.

Design notes:
    - Stateless: each call constructs no per-request state beyond
      what the splitter holds. The splitter itself is built once at
      service construction and reused.
    - Metadata-attached: each chunk carries its source document_id
      and sequential index. Without these, chunks become anonymous
      and unattributable downstream — fatal for citation in RAG.
    - Configurable: chunk_size and chunk_overlap come from Settings,
      not hardcoded. This is required for the evaluation phase where
      H1 (faithfulness +0.20 with RAG) is measured across parameter
      sweeps.
"""

from __future__ import annotations

from uuid import UUID

from langchain_text_splitters import RecursiveCharacterTextSplitter

from api.utils.config import Settings, get_settings
from api.models.document_schemas import DocumentChunk
from api.utils.logger import logger


class ChunkingService:
    """Splits text into overlapping chunks with attached metadata."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the service with chunking configuration.

        Args:
            settings: Application configuration. Reads ``chunk_size``
                and ``chunk_overlap``. Both are captured once at
                construction; runtime config changes require a restart.

        Raises:
            ValueError: If chunk_overlap >= chunk_size (LangChain's own
                check, propagated here for clarity).
        """
        self._chunk_size: int = settings.chunk_size
        self._chunk_overlap: int = settings.chunk_overlap

        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            # Separator priority: split on paragraph breaks first, then
            # sentences, then words, then characters. This is the default
            # but spelled out for transparency in the thesis methodology.
            separators=["\n\n", "\n", ". ", " ", ""],
            length_function=len,
            is_separator_regex=False,
        )

        logger.info(
            "ChunkingService initialized, chunk_size=%d, chunk_overlap=%d",
            self._chunk_size,
            self._chunk_overlap,
        )

    def chunk(self, document_id: UUID, text: str) -> list[DocumentChunk]:
        """Split text into chunks with metadata attached.

        Args:
            document_id: UUID of the source document. Embedded in each
                chunk's metadata for downstream attribution.
            text: The full extracted text to split.

        Returns:
            A list of DocumentChunk objects, in source order. Empty list
            if the input text is empty or contains only whitespace.
        """
        if not text or not text.strip():
            logger.warning(
                "Chunking called with empty text for document id=%s",
                document_id,
            )
            return []

        raw_chunks: list[str] = self._splitter.split_text(text)

        chunks = [
            DocumentChunk(
                document_id=document_id,
                index=position,
                content=content,
                char_count=len(content),
            )
            for position, content in enumerate(raw_chunks)
        ]

        logger.info(
            "Chunked document id=%s total_chars=%d chunks=%d",
            document_id,
            len(text),
            len(chunks),
        )
        return chunks


# ---------------------------------------------------------------------- #
# Dependency-injection provider
# ---------------------------------------------------------------------- #

_chunking_service_singleton: ChunkingService | None = None


def get_chunking_service() -> ChunkingService:
    """Return the process-wide singleton ChunkingService.

    Lazily constructed on first call. Use via FastAPI's ``Depends()``
    in route handlers.
    """
    global _chunking_service_singleton
    if _chunking_service_singleton is None:
        _chunking_service_singleton = ChunkingService(get_settings())
    return _chunking_service_singleton