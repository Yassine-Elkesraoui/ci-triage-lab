"""Document parsing service.

Locates uploaded files by their UUID and extracts plain text content,
dispatching to the appropriate parser based on file extension.

Design notes:
    - Stateless: each call resolves the file from disk fresh. No
      in-memory cache. The filesystem is the source of truth.
    - Registry pattern: parsers are registered in a dict keyed by
      extension. Adding support for .docx, .html, etc. later means
      one new function and one dict entry, no changes to call sites.
    - Defensive lookup: file resolution uses a glob, not a hardcoded
      extension, because the route receives only the UUID. The
      service must discover which extension was used at upload time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from uuid import UUID

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError
from api.utils.config import Settings, get_settings
from api.exceptions.document_exceptions import (
    DocumentNotFoundError,
    DocumentParsingError,
)
from api.utils.logger import logger


# Type alias: every parser takes a Path and returns extracted text.
# Kept narrow on purpose — parsers MUST NOT do I/O elsewhere, log to
# external systems, or have side effects. Pure file-in, text-out.
ParserFn = Callable[[Path], str]


# ---------------------------------------------------------------------- #
# Parser implementations
# ---------------------------------------------------------------------- #


def _parse_text_file(path: Path) -> str:
    """Read a plain-text or markdown file as UTF-8.

    Raises:
        DocumentParsingError: If the file cannot be decoded as UTF-8.
            Other encodings (latin-1, Windows-1252) are not auto-detected
            because silent encoding guesswork causes data corruption that
            is invisible until retrieval quality degrades.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentParsingError(
            f"File is not valid UTF-8. Re-save as UTF-8 and re-upload. "
            f"Decoding error at byte {exc.start}: {exc.reason}"
        ) from exc


def _parse_pdf_file(path: Path) -> str:
    """Extract text from a PDF using pypdf.

    Concatenates per-page text with double-newlines as page separators.
    Page boundaries are intentionally preserved as paragraph breaks so
    the downstream chunker can use them as natural split points.

    Raises:
        DocumentParsingError: If the PDF is encrypted, corrupted, or
            otherwise unreadable. The original exception is chained
            for log forensics.
    """
    try:
        reader = PdfReader(str(path))
    except (PdfReadError, PdfStreamError, OSError) as exc:
        raise DocumentParsingError(
            f"Failed to open PDF: {exc}"
        ) from exc

    if reader.is_encrypted:
        raise DocumentParsingError(
            "PDF is password-protected. Encrypted PDFs are not supported."
        )

    pages: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf can raise broad exceptions per page
            logger.warning(
                "Skipping unreadable page %d in PDF: %s", page_number, exc
            )
            continue
        if text.strip():
            pages.append(text)

    if not pages:
        raise DocumentParsingError(
            "PDF contains no extractable text. It may be a scanned image "
            "(OCR required) or use unsupported font encodings."
        )

    return "\n\n".join(pages)


# ---------------------------------------------------------------------- #
# Parser registry — single source of truth for supported types
# ---------------------------------------------------------------------- #

_PARSERS: dict[str, ParserFn] = {
    ".txt": _parse_text_file,
    ".md": _parse_text_file,
    ".pdf": _parse_pdf_file,
}


# ---------------------------------------------------------------------- #
# Service class
# ---------------------------------------------------------------------- #


class ParsingService:
    """Resolves uploaded files by UUID and extracts their text content."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the service with application settings.

        Args:
            settings: Application configuration. Only ``upload_dir`` is
                read; the value is captured at construction time.
        """
        self._upload_dir: Path = Path(settings.upload_dir)
        logger.info(
            "ParsingService initialized, upload_dir=%s", self._upload_dir
        )

    def parse(self, document_id: UUID) -> str:
        """Locate a document by UUID and return its extracted text.

        Args:
            document_id: The UUID assigned at upload time.

        Returns:
            The extracted plain text content.

        Raises:
            DocumentNotFoundError: If no file matching the UUID exists.
            DocumentParsingError: If the file exists but cannot be parsed
                (unsupported extension, encrypted PDF, corrupted, etc.).
        """
        path = self._resolve_path(document_id)
        parser = self._select_parser(path)

        logger.info(
            "Parsing document id=%s ext=%s", document_id, path.suffix.lower()
        )
        text = parser(path)
        logger.info(
            "Parsed document id=%s extracted_chars=%d",
            document_id,
            len(text),
        )
        return text

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _resolve_path(self, document_id: UUID) -> Path:
        """Find the file matching a UUID, regardless of its extension.

        The upload flow saves files as ``<uuid>.<ext>``, but the route
        layer receives only the UUID. A glob lookup discovers the
        extension that was actually used.

        Raises:
            DocumentNotFoundError: If no file or multiple files match.
                Multiple matches would indicate a serious storage bug
                (two extensions for the same UUID); raising is safer
                than silently picking one.
        """
        matches = list(self._upload_dir.glob(f"{document_id}.*"))
        if not matches:
            raise DocumentNotFoundError(
                f"No document found with id '{document_id}'."
            )
        if len(matches) > 1:
            raise DocumentNotFoundError(
                f"Multiple files found for id '{document_id}'. "
                f"Storage state is inconsistent; manual inspection needed."
            )
        return matches[0]

    @staticmethod
    def _select_parser(path: Path) -> ParserFn:
        """Look up the parser for a file based on its extension.

        Raises:
            DocumentParsingError: If no parser is registered for the
                extension. This shouldn't happen in practice — upload
                validation already restricts extensions — but is checked
                defensively in case the registry and the upload allow-list
                drift out of sync.
        """
        ext = path.suffix.lower()
        parser = _PARSERS.get(ext)
        if parser is None:
            raise DocumentParsingError(
                f"No parser registered for extension '{ext}'."
            )
        return parser


# ---------------------------------------------------------------------- #
# Dependency-injection provider
# ---------------------------------------------------------------------- #

_parsing_service_singleton: ParsingService | None = None


def get_parsing_service() -> ParsingService:
    """Return the process-wide singleton ParsingService.

    Lazily constructed on first call. Use via FastAPI's ``Depends()``
    in route handlers.
    """
    global _parsing_service_singleton
    if _parsing_service_singleton is None:
        _parsing_service_singleton = ParsingService(get_settings())
    return _parsing_service_singleton