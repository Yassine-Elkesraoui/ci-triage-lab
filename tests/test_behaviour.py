"""Behaviour tests for schemas, chunking and settings.

Unlike test_app.py (structural smoke tests), these exercise real logic
so that regressions in business code are actually caught by CI.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.models.schemas import ChatRequest, ChatResponse
from api.services.chunking_service import ChunkingService
from api.utils.config import Settings


# --------------------------------------------------------------- #
# ChatRequest / ChatResponse schema contract
# --------------------------------------------------------------- #

def test_chat_request_accepts_valid_question() -> None:
    import random
    question = random.choice(["RAG", "LLM", "NLP", "AI", "ML", "QA"])
    request = ChatRequest(question=question)
    assert request.question == question


def test_chat_request_rejects_short_question() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="hi")


def test_chat_request_rejects_overlong_question() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="x" * 2001)


def test_chat_response_requires_all_fields() -> None:
    response = ChatResponse(question="q", answer="a", status="success")
    assert response.status == "success"
    with pytest.raises(ValidationError):
        ChatResponse(question="q", answer="a")


# --------------------------------------------------------------- #
# ChunkingService behaviour
# --------------------------------------------------------------- #

def _service(chunk_size: int = 100, chunk_overlap: int = 20) -> ChunkingService:
    settings = Settings(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return ChunkingService(settings)


def test_chunking_returns_empty_list_for_blank_text() -> None:
    assert _service().chunk(uuid4(), "   ") == []


def test_chunking_splits_long_text_into_multiple_chunks() -> None:
    text = "Cloud computing is a model for delivering resources. " * 20
    chunks = _service().chunk(uuid4(), text)
    assert len(chunks) > 1


def test_chunking_attaches_sequential_indexes_and_document_id() -> None:
    document_id = uuid4()
    text = "Retrieval augmented generation grounds answers in sources. " * 20
    chunks = _service().chunk(document_id, text)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.document_id == document_id for chunk in chunks)


def test_chunking_char_count_matches_content_length() -> None:
    text = "Vector databases store embeddings for similarity search. " * 20
    chunks = _service().chunk(uuid4(), text)
    assert all(chunk.char_count == len(chunk.content) for chunk in chunks)


# --------------------------------------------------------------- #
# Settings defaults
# --------------------------------------------------------------- #

def test_settings_defaults_are_sane() -> None:
    settings = Settings()
    assert settings.chunk_overlap < settings.chunk_size
    assert 0.0 <= settings.retrieval_min_score <= 1.0
    assert settings.retrieval_top_k > 0
