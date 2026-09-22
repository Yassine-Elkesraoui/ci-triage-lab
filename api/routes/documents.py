"""HTTP routes for document upload and management.

This module is the HTTP boundary for the documents domain. It owns:
  - Translating multipart/form-data into a service-layer call
  - Mapping domain exceptions to HTTP status codes
  - Declaring the response contract via Pydantic

It does NOT own:
  - File validation rules (lives in DocumentService)
  - Storage strategy (lives in DocumentService)
  - Parsing or chunking logic (lives in their respective services)
  - Vector store operations (lives in VectorStoreService)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from api.exceptions.document_exceptions import (
    ChromaDBError,
    DocumentNotFoundError,
    DocumentParsingError,
    DocumentStorageError,
    DocumentValidationError,
    EmbeddingError,
)
from api.models.document_schemas import (
    DocumentResponse,
    ProcessedDocumentResponse,
    StoredChunk,
)
from api.services.chunking_service import ChunkingService, get_chunking_service
from api.services.document_service import DocumentService, get_document_service
from api.services.embedding_service import EmbeddingService, get_embedding_service
from api.services.parsing_service import ParsingService, get_parsing_service
from api.services.vector_store_service import (
    VectorStoreService,
    get_vector_store_service,
)
from api.utils.logger import logger


router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document for indexing",
    responses={
        201: {"description": "Document uploaded and saved to disk."},
        422: {"description": "File failed validation (type, size, or empty)."},
        500: {"description": "Storage failure on the server."},
    },
)
async def upload_document(
    file: UploadFile = File(
        ...,
        description="The document to upload. Allowed: .pdf, .txt, .md. Max 10 MB.",
    ),
    service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    """Upload and persist a document.

    The document is validated, assigned a UUID, and stored on local disk
    under the configured upload directory. The response contains the
    UUID needed to reference the document in subsequent API calls.

    Note:
        This endpoint only stores the file. Parsing, chunking, embedding,
        and persistence to the vector store happen when
        POST /documents/{id}/process is called.
    """
    try:
        return await service.save_upload(file)
    except DocumentValidationError as exc:
        logger.warning("Document validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except DocumentStorageError as exc:
        logger.error("Document storage failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save the uploaded file. Please try again.",
        ) from exc


@router.post(
    "/{document_id}/process",
    response_model=ProcessedDocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Parse, chunk, embed, and persist an uploaded document",
    responses={
        200: {"description": "Document processed and chunks persisted to the vector store."},
        404: {"description": "No document found with the given UUID."},
        422: {"description": "Document content could not be parsed."},
        500: {"description": "Embedding or vector store failure."},
    },
)
async def process_document(
    document_id: UUID,
    parsing_service: ParsingService = Depends(get_parsing_service),
    chunking_service: ChunkingService = Depends(get_chunking_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_store: VectorStoreService = Depends(get_vector_store_service),
) -> ProcessedDocumentResponse:
    """Parse a previously uploaded document, chunk it, embed each chunk,
    and persist the chunks to the vector store.

    This endpoint orchestrates the four stages of document preparation:
      1. Resolve the file on disk by UUID and extract its text content.
      2. Split the text into overlapping chunks.
      3. Embed each chunk into a dense vector.
      4. Persist the embedded chunks to ChromaDB (upsert by chunk_id).

    The endpoint is idempotent: calling it twice for the same document
    overwrites existing chunks at the same chunk_id rather than
    duplicating them.

    Args:
        document_id: UUID of a previously uploaded document.

    Returns:
        ProcessedDocumentResponse with summary stats and the chunks
        themselves (without embedding vectors).

    Raises:
        HTTPException: 404 if the document UUID is unknown.
        HTTPException: 422 if the document content cannot be parsed.
        HTTPException: 500 if embedding or persistence fails.
    """
    try:
        text = parsing_service.parse(document_id)
        chunks = chunking_service.chunk(document_id, text)
    except DocumentNotFoundError as exc:
        logger.warning("Document not found: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except DocumentParsingError as exc:
        logger.warning("Document parsing failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Document produced no chunks. The file may be empty "
                   "or contain no extractable text.",
        )

    try:
        embedded_chunks = embedding_service.embed_chunks(chunks)
        vector_store.upsert_chunks(embedded_chunks)
    except EmbeddingError as exc:
        logger.error("Embedding failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to embed document chunks. Please try again.",
        ) from exc
    except ChromaDBError as exc:
        logger.error("Vector store persistence failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist chunks to the vector store. Please try again.",
        ) from exc

    total_chars = sum(c.char_count for c in chunks)
    return ProcessedDocumentResponse(
        document_id=document_id,
        total_chars=total_chars,
        chunk_count=len(chunks),
        persisted=True,
        chunks=chunks,
    )


@router.get(
    "/{document_id}/chunks",
    response_model=list[StoredChunk],
    status_code=status.HTTP_200_OK,
    summary="List stored chunks for a document",
    responses={
        200: {"description": "List of chunks (possibly empty if document has not been processed)."},
        404: {"description": "No chunks found for the given UUID (document never processed)."},
        500: {"description": "Vector store query failure."},
    },
)
async def list_document_chunks(
    document_id: UUID,
    vector_store: VectorStoreService = Depends(get_vector_store_service),
) -> list[StoredChunk]:
    """Return all stored chunks for a document, ordered by index.

    This is a read-only inspection endpoint. It does NOT trigger
    processing; if a document has never been processed (or its chunks
    were deleted), this returns 404, not 200 with an empty list.

    The response excludes embedding vectors to keep payloads small.
    Each chunk's chunk_id, document_id, index, content, char_count, and
    model_name are returned.

    Args:
        document_id: UUID of a previously processed document.

    Returns:
        Ordered list of StoredChunk objects.

    Raises:
        HTTPException: 404 if the document has no stored chunks.
        HTTPException: 500 if the vector store query fails.
    """
    try:
        chunks = vector_store.get_chunks_for_document(document_id)
    except ChromaDBError as exc:
        logger.error("Vector store query failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve chunks from the vector store.",
        ) from exc

    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No chunks found for document id '{document_id}'. "
                   f"The document may not have been processed yet.",
        )

    return chunks
