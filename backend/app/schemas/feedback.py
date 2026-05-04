"""Pydantic schemas for user correction feedback logs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCreate(BaseModel):
    """Incoming payload for correcting an agent-generated post."""

    user_id: int
    corrected_text: str = Field(..., min_length=1)


class FeedbackLogOut(BaseModel):
    """Public response model for continual-learning feedback data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    user_id: int
    original_text: str
    corrected_text: str
    timestamp: datetime
    context_embedding_id: str | None = None
