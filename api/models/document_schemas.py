"""Pydantic schemas for document upload, management, and RAG chat.

These models define the request/response contracts for the API.
Internal storage details (e.g., absolute file paths) are deliberately
excluded from response models to prevent information disclosure.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentStatus(str, Enum):
    """Lifecycle states of an uploaded document."""

    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class DocumentResponse(BaseModel):
    """Public response schema returned after a successful document upload."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "filename": "research_paper.pdf",
                "file_size": 245678,
                "content_type": "application/pdf",
                "status": "uploaded",
                "uploaded_at": "2026-05-26T14:32:00Z",
            }
        }
    )

    id: UUID = Field(..., description="UUID4 assigned to the document on upload.")
    filename: str = Field(..., min_length=1, max_length=255, description="Original filename as provided by the client.")
    file_size: int = Field(..., gt=0, description="Size of the uploaded file in bytes.")
    content_type: str = Field(..., description="MIME type of the uploaded file.")
    status: DocumentStatus = Field(default=DocumentStatus.UPLOADED, description="Current lifecycle status of the document.")
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of when the upload completed.")


class DocumentChunk(BaseModel):
    """A single text chunk extracted from a document, pre-embedding."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "index": 0,
                "content": "This is the first chunk of the document...",
                "char_count": 1000,
            }
        }
    )

    document_id: UUID = Field(..., description="UUID of the source document this chunk was extracted from.")
    index: int = Field(..., ge=0, description="Zero-based position of this chunk within the source document.")
    content: str = Field(..., min_length=1, description="The raw text content of the chunk.")
    char_count: int = Field(..., gt=0, description="Length of the chunk content in characters.")


class ProcessedDocumentResponse(BaseModel):
    """Response returned after a document has been parsed, chunked, embedded, and persisted."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "total_chars": 12450,
                "chunk_count": 14,
                "persisted": True,
                "chunks": [],
            }
        }
    )

    document_id: UUID = Field(..., description="UUID of the processed document.")
    total_chars: int = Field(..., ge=0, description="Total characters of extracted text across all chunks (approximate).")
    chunk_count: int = Field(..., ge=0, description="Number of chunks produced.")
    persisted: bool = Field(default=True, description="Whether chunks were successfully written to the vector store.")
    chunks: list[DocumentChunk] = Field(..., description="Ordered list of chunks, indexed from 0. Embedding vectors omitted for response size.")


class EmbeddedChunk(BaseModel):
    """A document chunk with its embedding vector attached."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "index": 0,
                "content": "First chunk of the document...",
                "char_count": 1000,
                "embedding": [0.123, -0.456, 0.789],
                "embedding_dimension": 384,
                "model_name": "sentence-transformers/all-MiniLM-L6-v2",
            }
        }
    )

    document_id: UUID = Field(..., description="UUID of the source document.")
    index: int = Field(..., ge=0, description="Zero-based position of this chunk within the source document.")
    content: str = Field(..., min_length=1, description="The raw text content of the chunk.")
    char_count: int = Field(..., gt=0, description="Length of the chunk content in characters.")
    embedding: list[float] = Field(..., description="The dense vector representation of the chunk content.")
    embedding_dimension: int = Field(..., gt=0, description="Dimensionality of the embedding vector.")
    model_name: str = Field(..., description="HuggingFace identifier of the embedding model used.")


class StoredChunk(BaseModel):
    """A chunk as stored in (and retrieved from) the vector store, without its vector."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "chunk_id": "550e8400-e29b-41d4-a716-446655440000::0",
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "index": 0,
                "content": "First chunk of the document...",
                "char_count": 1000,
                "model_name": "sentence-transformers/all-MiniLM-L6-v2",
            }
        }
    )

    chunk_id: str = Field(..., min_length=1, description="Stable identifier in the form '{document_id}::{index}'.")
    document_id: UUID = Field(..., description="UUID of the source document.")
    index: int = Field(..., ge=0, description="Zero-based position within the source document.")
    content: str = Field(..., min_length=1, description="The raw text content of the chunk.")
    char_count: int = Field(..., gt=0, description="Length of the chunk content in characters.")
    model_name: str = Field(..., description="HuggingFace identifier of the embedding model used to embed this chunk.")


class RetrievedChunk(BaseModel):
    """A chunk retrieved by semantic search, with its similarity score.

    Completes the chunk data model:
        DocumentChunk -> EmbeddedChunk -> StoredChunk -> RetrievedChunk

    The score is cosine similarity in [0, 1] for normalized embeddings;
    higher means more relevant to the query. Returned as part of a RAG
    response so callers can inspect (and cite) the sources behind an answer.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "chunk_id": "550e8400-e29b-41d4-a716-446655440000::3",
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "index": 3,
                "content": "The relevant passage that matched the query...",
                "score": 0.7421,
            }
        }
    )

    chunk_id: str = Field(..., min_length=1, description="Stable identifier in the form '{document_id}::{index}'.")
    document_id: UUID = Field(..., description="UUID of the source document.")
    index: int = Field(..., ge=0, description="Zero-based position within the source document.")
    content: str = Field(..., min_length=1, description="The text content of the retrieved chunk.")
    score: float = Field(..., description="Cosine similarity to the query in [0, 1]. Higher is more relevant.")


class RAGChatRequest(BaseModel):
    """Request body for the RAG-enabled chat endpoint."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "question": "Como recupero a minha senha do Portal das Financas?",
                "document_id": None,
                "top_k": 4,
            }
        }
    )

    question: str = Field(..., min_length=1, max_length=2000, description="The user's question.")
    document_id: UUID | None = Field(default=None, description="Optional: scope retrieval to a single document. If omitted, searches the whole corpus.")
    top_k: int | None = Field(default=None, ge=1, le=20, description="Optional: number of chunks to retrieve. Falls back to the configured default if omitted.")


class RAGChatResponse(BaseModel):
    """Response from the RAG-enabled chat endpoint."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "answer": "Para recuperar a senha, aceda ao Portal das Financas e...",
                "grounded": True,
                "sources": [],
                "model": "phi3",
            }
        }
    )

    answer: str = Field(..., description="The generated answer.")
    grounded: bool = Field(..., description="True if the answer was generated using retrieved context. False if no chunk cleared the relevance threshold and the model answered from its own knowledge.")
    sources: list[RetrievedChunk] = Field(..., description="The chunks used as context, ordered by relevance. Empty when grounded is False.")
    model: str = Field(..., description="Identifier of the LLM that generated the answer.")
