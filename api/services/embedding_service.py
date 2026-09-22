"""Text-to-vector embedding service.

Wraps sentence-transformers to convert document chunks into dense
vectors suitable for similarity search. Embeddings are normalized
to unit length so cosine similarity reduces to a dot product.

Design notes:
    - Eager loading: the model is loaded once at service construction
      (singleton). First-call latency is paid at startup, not at user
      request time. Startup failures (missing files, network issues)
      become visible at boot.
    - Sync API: sentence-transformers is synchronous. Calls from async
      route handlers will briefly block the event loop. Acceptable at
      single-user MVP scale; revisit if concurrent load matters.
    - Project-local cache: model files live in settings.model_cache_dir
      (default "models/") rather than the user-global HuggingFace
      cache. Keeps the project self-contained and gitignored.
    - Normalized output: vectors are L2-normalized at encode time.
      Cosine similarity on normalized vectors equals their dot product,
      which is faster and the ChromaDB default on Day 12.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from sentence_transformers import SentenceTransformer

from api.exceptions.document_exceptions import EmbeddingError
from api.models.document_schemas import DocumentChunk, EmbeddedChunk
from api.utils.config import Settings, get_settings
from api.utils.logger import logger


class EmbeddingService:
    """Converts text chunks into normalized dense vectors."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the service and load the embedding model.

        The model is downloaded from HuggingFace on first run (~90 MB
        for all-MiniLM-L6-v2) and cached under settings.model_cache_dir
        for subsequent runs.

        Args:
            settings: Application configuration. Reads embedding_*
                fields plus model_cache_dir.

        Raises:
            EmbeddingError: If the cache directory cannot be created
                or the model fails to load.
        """
        self._model_name: str = settings.embedding_model_name
        self._batch_size: int = settings.embedding_batch_size
        self._dimension: int = settings.embedding_dimension
        self._normalize: bool = settings.embedding_normalize
        self._device: str = settings.embedding_device
        self._cache_dir: Path = Path(settings.model_cache_dir)

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EmbeddingError(
                f"Cannot create model cache directory "
                f"{self._cache_dir!s}: {exc}"
            ) from exc

        logger.info(
            "Loading embedding model %s (device=%s, cache=%s)",
            self._model_name,
            self._device,
            self._cache_dir,
        )

        try:
            self._model = SentenceTransformer(
                model_name_or_path=self._model_name,
                cache_folder=str(self._cache_dir),
                device=self._device,
            )
        except Exception as exc:
            raise EmbeddingError(
                f"Failed to load embedding model {self._model_name!s}: {exc}"
            ) from exc

        actual_dim = self._model.get_sentence_embedding_dimension()
        if actual_dim != self._dimension:
            raise EmbeddingError(
                f"Configured embedding_dimension={self._dimension} does not "
                f"match model output dimension={actual_dim}. Update Settings "
                f"or change embedding_model_name."
            )

        logger.info(
            "EmbeddingService ready: model=%s, dim=%d, batch_size=%d, normalize=%s",
            self._model_name,
            self._dimension,
            self._batch_size,
            self._normalize,
        )

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode a sequence of texts into embedding vectors.

        Args:
            texts: Non-empty sequence of strings to encode. Each text
                may be of arbitrary length; sentence-transformers will
                truncate beyond the model's context window (~256 tokens
                for MiniLM-L6).

        Returns:
            A list of embedding vectors. Each vector is a list of floats
            of length self._dimension. Order matches input order.

        Raises:
            EmbeddingError: If the input is empty or encoding fails.
        """
        if not texts:
            raise EmbeddingError(
                "embed_texts called with empty input. This indicates a "
                "programmer error; the chunker should never produce zero "
                "chunks for a non-empty document."
            )

        try:
            vectors = self._model.encode(
                list(texts),
                batch_size=self._batch_size,
                normalize_embeddings=self._normalize,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingError(
                f"Encoding {len(texts)} text(s) failed: {exc}"
            ) from exc

        # sentence-transformers returns a numpy array; convert to plain
        # Python lists so downstream code (Pydantic, JSON, ChromaDB) does
        # not need numpy as a hard dependency.
        return [vector.tolist() for vector in vectors]

    def embed_chunks(self, chunks: Sequence[DocumentChunk]) -> list[EmbeddedChunk]:
        """Embed a sequence of DocumentChunks, returning EmbeddedChunks.

        The chunk metadata (document_id, index, content, char_count) is
        carried through unchanged; the embedding vector and model
        identifier are added.

        Args:
            chunks: Non-empty sequence of pre-embedding chunks.

        Returns:
            A list of EmbeddedChunks in the same order as input.

        Raises:
            EmbeddingError: If the input is empty or encoding fails.
        """
        if not chunks:
            raise EmbeddingError("embed_chunks called with empty input.")

        vectors = self.embed_texts([chunk.content for chunk in chunks])

        embedded = [
            EmbeddedChunk(
                document_id=chunk.document_id,
                index=chunk.index,
                content=chunk.content,
                char_count=chunk.char_count,
                embedding=vector,
                embedding_dimension=self._dimension,
                model_name=self._model_name,
            )
            for chunk, vector in zip(chunks, vectors)
        ]

        logger.info(
            "Embedded %d chunks for document id=%s",
            len(embedded),
            embedded[0].document_id,
        )
        return embedded

    @property
    def dimension(self) -> int:
        """The dimensionality of vectors this service produces."""
        return self._dimension

    @property
    def model_name(self) -> str:
        """The HuggingFace identifier of the loaded model."""
        return self._model_name


# ---------------------------------------------------------------------- #
# Dependency-injection provider
# ---------------------------------------------------------------------- #

_embedding_service_singleton: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Return the process-wide singleton EmbeddingService.

    Lazily constructed on first call. Use via FastAPI's Depends()
    in route handlers (Day 12+).
    """
    global _embedding_service_singleton
    if _embedding_service_singleton is None:
        _embedding_service_singleton = EmbeddingService(get_settings())
    return _embedding_service_singleton
