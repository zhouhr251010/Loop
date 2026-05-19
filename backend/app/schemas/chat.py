"""Pydantic schemas for daily private sync chats."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChatModelChoice = Literal["fast", "deep"]
ExperimentMode = Literal["mode_alpha", "mode_beta"]
SessionType = Literal[
    "Human_to_Agent",
    "Human_to_Human",
    "Agent_to_Agent",
    "Group_Shared",
]


class ChatMessageCreate(BaseModel):
    """Incoming private chat message from the user."""

    message: str = Field(..., min_length=1, max_length=4000)
    branch_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field("default_session", min_length=1, max_length=64)
    topic: str = Field("general", min_length=1, max_length=64)
    model: ChatModelChoice = "fast"
    experiment_mode: ExperimentMode = "mode_alpha"
    session_type: SessionType = "Human_to_Agent"

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> str:
        normalized = str(value or "general").strip()
        return normalized[:64] or "general"


class HumanChatMessageCreate(BaseModel):
    """Incoming human-to-human chat message."""

    receiver_user_id: int = Field(..., gt=0)
    content: str = Field(..., min_length=1, max_length=4000)
    branch_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field("default_human_session", min_length=1, max_length=64)
    topic: str = Field("human_peer", min_length=1, max_length=64)

    @field_validator("content", "branch_id", "session_id", "topic", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized

    @field_validator("topic", mode="after")
    @classmethod
    def limit_topic(cls, value: str) -> str:
        return value[:64] or "human_peer"


class ChatMemoryDiagnostic(BaseModel):
    """One memory/debug fragment shown in the chat developer panel."""

    kind: Literal["identity", "semantic", "episodic"]
    summary: str


class ChatLogOut(BaseModel):
    """Stored chat log response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    sender_user_id: int | None = None
    receiver_user_id: int | None = None
    group_id: str | None = None
    user_message: str
    agent_reply: str
    timestamp: datetime
    branch_id: str = "main"
    session_id: str = "default_session"
    topic: str = "general"
    experiment_mode: ExperimentMode = "mode_alpha"
    session_type: SessionType = "Human_to_Agent"
    is_read: bool = False


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
