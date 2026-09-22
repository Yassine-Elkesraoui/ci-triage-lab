"""Startup corpus seeding — self-healing for ephemeral deployments.

Hugging Face free Spaces wipe the container filesystem on every rebuild
and sleep/wake cycle, which empties ChromaDB's persisted store. This
module restores it: on application startup, IF the collection is empty,
the corpus files baked into the Docker image are ingested through the
SAME parse -> chunk -> embed -> upsert pipeline that serves
POST /documents/{id}/process. No logic is duplicated; the seed only
orchestrates the existing services.

Idempotency: the empty-collection guard makes this a no-op wherever the
store already holds data (e.g. local development). On a freshly wiped
container it rebuilds the store from the image's own corpus, so the
public demo is reproducible from scratch.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from api.services.chunking_service import get_chunking_service
from api.services.embedding_service import get_embedding_service
from api.services.parsing_service import get_parsing_service
from api.services.vector_store_service import get_vector_store_service
from api.utils.config import get_settings
from api.utils.logger import logger


def seed_corpus_if_empty() -> int:
    """Ingest baked-in corpus files if the vector store is empty.

    Returns:
        The number of chunks ingested (0 when the guard skips seeding
        or no corpus files are present).
    """
    settings = get_settings()
    vector_store = get_vector_store_service()

    existing = vector_store.total_chunks
    if existing > 0:
        logger.info(
            "Corpus seed skipped: vector store already holds %d chunks.",
            existing,
        )
        return 0

    corpus_dir = Path(settings.corpus_dir)
    corpus_files = sorted(corpus_dir.glob("*.txt"))
    if not corpus_files:
        logger.warning(
            "Corpus seed skipped: vector store is EMPTY and no corpus "
            "files were found in %s. /chat/rag will answer ungrounded "
            "until documents are uploaded.",
            corpus_dir,
        )
        return 0

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    parsing = get_parsing_service()
    chunking = get_chunking_service()
    embedding = get_embedding_service()

    total_ingested = 0
    for src in corpus_files:
        document_id = uuid4()
        target = upload_dir / f"{document_id}{src.suffix.lower()}"
        try:
            # Place the file where ParsingService resolves by UUID glob,
            # then run the standard pipeline on it.
            shutil.copyfile(src, target)
            text = parsing.parse(document_id)
            chunks = chunking.chunk(document_id, text)
            embedded = embedding.embed_chunks(chunks)
            vector_store.upsert_chunks(embedded)
        except Exception:
            logger.exception(
                "Corpus seed FAILED for %s (document_id=%s); "
                "continuing with remaining files.",
                src.name,
                document_id,
            )
            target.unlink(missing_ok=True)
            continue

        total_ingested += len(chunks)
        logger.info(
            "Seeded %s -> document_id=%s (%d chunks).",
            src.name,
            document_id,
            len(chunks),
        )

    logger.info(
        "Corpus seeding complete: %d file(s), collection %r now holds %d chunks.",
        len(corpus_files),
        vector_store.collection_name,
        vector_store.total_chunks,
    )
    return total_ingested
