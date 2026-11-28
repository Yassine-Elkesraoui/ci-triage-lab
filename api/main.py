"""
Entry point of the RAG Chatbot API.

This module is intentionally minimal. Its only responsibilities are:
    1. Create the FastAPI application instance using the centralized settings
    2. Register all routers (chat, future routes for documents, admin, etc.)
    3. Define general-purpose endpoints (root and health check)

Business logic lives in dedicated modules under api/routes/.
Data schemas live under api/models/.
Utility functions (logging, configuration) live under api/utils/.

This separation of concerns, combined with environment-based configuration,
follows the 12-Factor App methodology (Wiggins, 2017) and keeps the
codebase scalable, secure, and maintainable.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes import chat, chat_ui, documents, eval_dashboard, rag_chat
from api.services.seed_service import seed_corpus_if_empty
from api.utils.config import get_settings
from api.utils.logger import logger


# Create the FastAPI application instance.
# All metadata is now loaded from the centralized settings, which
# in turn read values from the .env file at the project root.
# This eliminates hardcoded values from the source code and enables
# environment-specific configuration without code changes.
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: runs once before the server starts serving.

    Seeds the vector store from the baked-in corpus when it is empty
    (self-healing on ephemeral filesystems such as HF Spaces).
    """
    seed_corpus_if_empty()
    yield


app = FastAPI(
    lifespan=lifespan,
    title=settings.app_name,
    description=(
        "Master's Thesis Project — Mohamed Yassine El Kesraoui, "
        "ISLA Polytechnic Institute of Management and Technology, "
        "Vila Nova de Gaia, Portugal."
    ),
    version=settings.app_version,
)


# Register the chat router. All endpoints defined in api/routes/chat.py
# are now exposed under the main application.
app.include_router(chat.router)    
app.include_router(documents.router)
app.include_router(rag_chat.router)
app.include_router(eval_dashboard.router)
app.include_router(chat_ui.router)

# Log the application startup with environment context.
# This message confirms which environment is active and helps debug
# misconfigurations during deployment.
logger.info(
    f"{settings.app_name} initialized — "
    f"version {settings.app_version}, "
    f"environment '{settings.app_env}'"
)


@app.get("/", tags=["General"])
def read_root() -> dict:
    """
    Root endpoint of the API.

    Returns a welcome message and runtime metadata about the service.
    Useful as a quick sanity check that the server is reachable and
    correctly configured.

    Returns:
        A dictionary containing the application name, status, version,
        and current environment.
    """
    return {
        "message": f"Welcome to the {settings.app_name}",
        "status": "running",
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@app.get("/health", tags=["General"])
def health_check() -> dict:
    """
    Health check endpoint for monitoring and load balancers.

    This endpoint is intentionally lightweight (no database calls,
    no external services) so it can be called frequently without
    impacting performance. It is the standard pattern used by
    Kubernetes, Docker, and cloud platforms to verify a service is alive.

    Returns:
        A dictionary indicating the service is healthy, including
        the current application version for traceability.
    """
    return {
        "status": "healthy",
        "version": settings.app_version,
    }
