"""Custom exceptions for document upload and management.

Following the same pattern as ollama_exceptions: domain-specific
exceptions that the route layer translates into appropriate HTTP
status codes. This keeps business logic decoupled from HTTP concerns.
"""


class DocumentError(Exception):
    """Base exception for all document-related errors."""


class DocumentValidationError(DocumentError):
    """Raised when an uploaded file fails validation.

    Examples:
        - Unsupported extension (e.g., .exe, .docx)
        - File exceeds maximum size
        - Empty file (zero bytes)
        - Filename contains illegal characters

    Maps to HTTP 422 Unprocessable Entity in the route layer.
    """


class DocumentStorageError(DocumentError):
    """Raised when persisting an uploaded file to disk fails.

    Examples:
        - Disk full / write permission denied
        - Upload directory missing or corrupted
        - I/O error during streaming write

    Maps to HTTP 500 Internal Server Error in the route layer.
    """


class DocumentNotFoundError(DocumentError):
    """Raised when a referenced document UUID has no file on disk.

    Examples:
        - User passes an unknown UUID to /documents/{id}/process
        - File was deleted between upload and processing
        - UUID is well-formed but never existed

    Maps to HTTP 404 Not Found in the route layer.
    """


class DocumentParsingError(DocumentError):
    """Raised when text extraction from a document fails.

    Examples:
        - PDF is encrypted / password-protected
        - PDF is corrupted or malformed
        - Text decoding fails (rare for UTF-8 input)
        - Underlying parser library raises an unexpected error

    Maps to HTTP 422 Unprocessable Entity.
    """


class EmbeddingError(DocumentError):
    """Raised when text-to-vector embedding fails.

    Examples:
        - Embedding model failed to load (file missing, corrupted, no network)
        - Out-of-memory during batch encoding
        - Underlying sentence-transformers raised an unexpected error
        - Input batch is empty (programmer error, not user input)

    Maps to HTTP 500 Internal Server Error when surfaced through routes.
    """


class ChromaDBError(DocumentError):
    """Raised when a ChromaDB operation fails.

    Examples:
        - Collection cannot be created or accessed
        - Upsert fails due to disk full, permissions, or DB corruption
        - Query returns malformed results (rare, indicates DB bug)
        - Dimension mismatch detected between collection and incoming vectors

    Maps to HTTP 500 Internal Server Error. Unlike validation errors,
    these are server-side storage failures the client cannot fix by
    sending different input. The detail message is sanitized in the
    route handler to avoid leaking storage internals.
    """
