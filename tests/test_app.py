"""Smoke tests for the RAG Chatbot Platform.

These tests verify the application is structurally sound without
requiring any external runtime (Ollama, Cerebras, ChromaDB network
access). They are designed to run in CI on a clean machine:

    - the FastAPI app imports and constructs,
    - all expected routes are registered,
    - the lightweight endpoints (/, /health) respond correctly.

LLM-dependent behaviour (/chat, /chat/rag) is intentionally NOT tested
here: those paths require a live provider and are validated manually
and via the deployed Space.
"""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_app_constructs() -> None:
    """The application object exists and carries metadata."""
    assert app.title


def test_expected_routes_registered() -> None:
    """All core endpoints are mounted."""
    paths = {route.path for route in app.routes}
    for expected in ("/", "/health", "/chat", "/chat/rag", "/eval/data", "/eval/dashboard"):
        assert expected in paths, f"missing route: {expected}"


def test_root_endpoint() -> None:
    """GET / returns the welcome payload."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"


def test_health_endpoint() -> None:
    """GET /health responds healthy without external dependencies."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
