"""
schemas.py
──────────
Pydantic data models defining the API contract for the /chat endpoint.

These models are validated automatically by FastAPI at the framework
boundary, so any request that violates the contract (e.g., empty question,
excessively long input) is rejected with HTTP 422 BEFORE reaching the
business logic. This eliminates a class of bugs and protects the LLM
from wasted inference on garbage input.
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    Schema for incoming chat requests.

    The `question` field has explicit minimum and maximum length constraints
    to reject malformed or abusive input at the framework boundary.

    Attributes:
        question: The user's natural-language question.
                  Must be between 3 and 2000 characters.
    """

    question: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="The user's question to the AI. Must be 3-2000 characters.",
        examples=["What is Retrieval-Augmented Generation?"],
    )


class ChatResponse(BaseModel):
    """
    Schema for outgoing chat responses.

    Attributes:
        question: Echo of the original user question (for client correlation).
        answer: The AI-generated answer from Phi-3 Mini.
        status: High-level status indicator ("success" on normal completion).
    """

    question: str
    answer: str
    status: str