"""Pydantic schemas for daily private sync chats."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChatModelChoice = Literal["fast", "deep"]
ExperimentMode = Literal["mode_alpha", "mode_beta"]


class ChatMessageCreate(BaseModel):
    """Incoming private chat message from the user."""

    message: str = Field(..., min_length=1, max_length=4000)
    branch_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field("default_session", min_length=1, max_length=64)
    topic: str = Field("general", min_length=1, max_length=64)
    model: ChatModelChoice = "fast"
    experiment_mode: ExperimentMode = "mode_alpha"

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> str:
        normalized = str(value or "general").strip()
        return normalized[:64] or "general"


class ChatMemoryDiagnostic(BaseModel):
    """One memory/debug fragment shown in the chat developer panel."""

    kind: Literal["identity", "semantic", "episodic"]
    summary: str


class ChatLogOut(BaseModel):
    """Stored chat log response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    user_message: str
    agent_reply: str
    timestamp: datetime
    branch_id: str = "main"
    session_id: str = "default_session"
    topic: str = "general"
    experiment_mode: ExperimentMode = "mode_alpha"


class ChatSessionOut(BaseModel):
    """Sidebar session summary for one branch-scoped chat session."""

    branch_id: str
    session_id: str
    first_message: str
    latest_message: str
    latest_timestamp: datetime
    turn_count: int


class ChatReplyOut(BaseModel):
    """Response returned after a private sync message."""

    reply: str
    chat_log: ChatLogOut | None = None
    memory_chunks_used: int = 0
    model_used: ChatModelChoice = "fast"
    stored: bool = True
    warning: str | None = None
    query_route: str = "Full IACL"
    memory_diagnostics: list[ChatMemoryDiagnostic] = Field(default_factory=list)


class DriftCheckCreate(BaseModel):
    """Branch context for an identity drift check."""

    branch_id: str = Field("main", min_length=1, max_length=128)
    session_id: str = Field("default_session", min_length=1, max_length=64)
    topic: str = Field("general", min_length=1, max_length=64)

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> str:
        normalized = str(value or "general").strip()
        return normalized[:64] or "general"


class DriftCheckOut(BaseModel):
    """Zero-shot drift detector result."""

    consistency_score: float = Field(..., ge=0.0, le=1.0)
    drift_probability: float = Field(..., ge=0.0, le=1.0)
    is_drifting: bool
    reason: str
