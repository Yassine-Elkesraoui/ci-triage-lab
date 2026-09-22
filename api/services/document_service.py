"""Document upload and storage service.

Handles validation, UUID assignment, and disk persistence of uploaded
files. Designed as a singleton injected via FastAPI's Depends() system,
mirroring the OllamaService pattern from Day 8.

Storage strategy:
    Files are saved to ``settings.upload_dir`` as ``<uuid>.<ext>``.
    The original filename is preserved only in the API response and
    (eventually) in the metadata database — never in the filesystem.
    This prevents path traversal attacks via crafted filenames and
    avoids collisions when two users upload "report.pdf".
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile

from api.utils.config import Settings, get_settings
from api.exceptions.document_exceptions import (
    DocumentStorageError,
    DocumentValidationError,
)
from api.models.document_schemas import DocumentResponse, DocumentStatus
from api.utils.logger import logger


# Whitelist of accepted extensions. Lowercase, with leading dot.
# Kept as a module-level constant (not a Settings field) because the
# RAG pipeline's parsers — added Day 10+ — are tightly coupled to
# these specific formats. Changing the set requires a code change,
# not a config tweak.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md"})

# 10 MB hard ceiling, expressed in bytes.
MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024

# Read in 1 MB chunks during streaming writes — balances memory
# footprint against syscall overhead for files up to 10 MB.
CHUNK_SIZE_BYTES: int = 1 * 1024 * 1024


class DocumentService:
    """Validates and persists uploaded documents to local disk.

    Designed for dependency injection: instantiated once per process
    via ``get_document_service()`` and reused across requests.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the service with application settings.

        Args:
            settings: Application configuration. The ``upload_dir``
                attribute is read once at construction time.

        Raises:
            DocumentStorageError: If the configured upload directory
                does not exist and cannot be created.
        """
        self._upload_dir: Path = Path(settings.upload_dir)
        try:
            self._upload_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DocumentStorageError(
                f"Cannot create or access upload directory "
                f"'{self._upload_dir}': {exc}"
            ) from exc
        logger.info(
            "DocumentService initialized, upload_dir=%s", self._upload_dir
        )

    async def save_upload(self, file: UploadFile) -> DocumentResponse:
        """Validate and persist an uploaded file.

        Performs three checks in order — extension, then size during
        streaming, then non-emptiness — before writing to disk. The
        file is assigned a fresh UUID4 and saved as
        ``<upload_dir>/<uuid>.<ext>``.

        Args:
            file: The multipart upload from FastAPI.

        Returns:
            DocumentResponse with the assigned UUID, original filename,
            byte size, MIME type, and ``UPLOADED`` status.

        Raises:
            DocumentValidationError: If the filename is missing, the
                extension is not allowed, the file exceeds
                ``MAX_FILE_SIZE_BYTES``, or the file is empty.
            DocumentStorageError: If a disk I/O failure occurs while
                writing. The partial file is cleaned up on failure.
        """
        # ---- Validation ----
        original_filename = self._validate_filename(file.filename)
        extension = Path(original_filename).suffix.lower()
        self._validate_extension(extension)

        # ---- Allocate identity & target path ----
        document_id = uuid4()
        target_path = self._upload_dir / f"{document_id}{extension}"

        # ---- Stream to disk with size enforcement ----
        bytes_written = await self._stream_to_disk(file, target_path)

        if bytes_written == 0:
            target_path.unlink(missing_ok=True)
            raise DocumentValidationError(
                f"Uploaded file '{original_filename}' is empty."
            )

        logger.info(
            "Saved upload id=%s filename=%s size=%d bytes",
            document_id,
            original_filename,
            bytes_written,
        )

        return DocumentResponse(
            id=document_id,
            filename=original_filename,
            file_size=bytes_written,
            content_type=file.content_type or "application/octet-stream",
            status=DocumentStatus.UPLOADED,
        )

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_filename(filename: str | None) -> str:
        """Ensure the upload has a non-empty filename.

        Raises:
            DocumentValidationError: If filename is None or blank.
        """
        if not filename or not filename.strip():
            raise DocumentValidationError(
                "Upload is missing a filename."
            )
        return filename

    @staticmethod
    def _validate_extension(extension: str) -> None:
        """Ensure the file extension is on the allow-list.

        Raises:
            DocumentValidationError: If extension is not allowed.
        """
        if extension not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise DocumentValidationError(
                f"Unsupported file extension '{extension}'. "
                f"Allowed extensions: {allowed}."
            )

    async def _stream_to_disk(
        self, file: UploadFile, target_path: Path
    ) -> int:
        """Stream ``file`` to ``target_path`` in 1 MB chunks.

        Enforces the size limit *during* the write rather than reading
        the full body into memory first. On any failure, the partial
        file is removed before the exception propagates.

        Returns:
            Total bytes written.

        Raises:
            DocumentValidationError: If the cumulative size exceeds
                ``MAX_FILE_SIZE_BYTES``.
            DocumentStorageError: On any underlying I/O failure.
        """
        bytes_written = 0
        try:
            with target_path.open("wb") as out:
                while chunk := await file.read(CHUNK_SIZE_BYTES):
                    bytes_written += len(chunk)
                    if bytes_written > MAX_FILE_SIZE_BYTES:
                        raise DocumentValidationError(
                            f"File exceeds maximum size of "
                            f"{MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."
                        )
                    out.write(chunk)
        except DocumentValidationError:
            target_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            target_path.unlink(missing_ok=True)
            raise DocumentStorageError(
                f"Failed to write upload to disk: {exc}"
            ) from exc

        return bytes_written


# ---------------------------------------------------------------------- #
# Dependency-injection provider
# ---------------------------------------------------------------------- #

_document_service_singleton: DocumentService | None = None


def get_document_service() -> DocumentService:
    """Return the process-wide singleton DocumentService.

    Lazily constructed on first call. Use via FastAPI's ``Depends()``
    in route handlers:

        @router.post("/documents")
        async def upload(
            file: UploadFile,
            service: DocumentService = Depends(get_document_service),
        ) -> DocumentResponse:
            return await service.save_upload(file)
    """
    global _document_service_singleton
    if _document_service_singleton is None:
        _document_service_singleton = DocumentService(get_settings())
    return _document_service_singleton