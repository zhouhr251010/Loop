"""Pydantic schemas for importing multi-source group chat history."""

from pydantic import BaseModel, Field, field_validator


class ImportedChatMessageCreate(BaseModel):
    """One cleaned group-chat message after frontend sender ID alignment."""

    sender_agent_id: int = Field(..., ge=1)
    content: str = Field(..., min_length=1, max_length=4000)
    timestamp: str | None = Field(default=None, max_length=128)

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        """Trim empty edges so imported memory does not store padding."""
        return str(value).strip()

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: str | None) -> str | None:
        """Preserve external timestamps as compact metadata strings."""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class ImportedChatBatchCreate(BaseModel):
    """Batch payload for target-agent perspective initialization."""

    branch_id: str = Field(
        default="main",
        description="The timeline/branch ID for this operation",
    )
    messages: list[ImportedChatMessageCreate] = Field(..., min_length=1, max_length=2000)
    topic: str | None = Field(default=None, max_length=80)

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: str | None) -> str | None:
        """Store an optional batch-level topic label as compact metadata."""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class ImportedChatBatchOut(BaseModel):
    """Response returned after perspective-isolated chat import."""

    message: str
    target_agent_id: int
    records_received: int
    chunks_added: int
    me_messages: int
    others_messages: int
