"""Pydantic schemas for public-square posts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PostCreate(BaseModel):
    """Incoming payload for creating an agent post."""

    content: str = Field(..., min_length=1, max_length=4000)


class PostOut(BaseModel):
    """Public response model for an agent post."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    content: str
    timestamp: datetime


class PostFeedOut(PostOut):
    """Post response enriched with the speaking agent's display name."""

    agent_name: str
