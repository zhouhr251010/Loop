"""Pydantic schemas for human-to-human social chat."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SocialContactOut(BaseModel):
    """Lightweight contact entry for the participant directory."""

    user_id: str
    username: str
    unread_count: int = 0


class SocialMessageCreate(BaseModel):
    """Incoming persisted human-to-human message."""

    receiver_user_id: int = Field(..., gt=0)
    content: str = Field(..., min_length=1, max_length=4000)
    branch_id: str = Field("main", min_length=1, max_length=128)
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


class SocialGroupCreate(BaseModel):
    """Create a human IM room from selected contact ids."""

    contact_ids: list[int] = Field(..., min_length=1, max_length=50)
    name: str | None = Field(None, max_length=128)

    @field_validator("contact_ids")
    @classmethod
    def dedupe_contact_ids(cls, value: list[int]) -> list[int]:
        seen_ids: set[int] = set()
        contact_ids: list[int] = []
        for contact_id in value:
            if contact_id <= 0 or contact_id in seen_ids:
                continue
            seen_ids.add(contact_id)
            contact_ids.append(contact_id)
        if not contact_ids:
            raise ValueError("Choose at least one contact.")
        return contact_ids

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized[:128] or None


class SocialGroupMessageCreate(BaseModel):
    """Incoming persisted human group message."""

    content: str = Field(..., min_length=1, max_length=4000)
    branch_id: str = Field("main", min_length=1, max_length=128)
    topic: str = Field("group_chat", min_length=1, max_length=64)

    @field_validator("content", "branch_id", "topic", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized

    @field_validator("topic", mode="after")
    @classmethod
    def limit_topic(cls, value: str) -> str:
        return value[:64] or "group_chat"


class SocialGroupOut(BaseModel):
    """One human chat room shown in the IM sidebar."""

    id: str
    name: str
    owner_id: int | None = None
    member_count: int
    member_ids: list[int]
    latest_message: str | None = None
    latest_timestamp: datetime | None = None


class SocialMessageOut(BaseModel):
    """Stored human-to-human message returned by REST and SSE push."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_id: int
    receiver_id: int | None = None
    sender_username: str
    receiver_username: str | None = None
    group_id: str | None = None
    content: str
    timestamp: datetime
    is_read: bool = False
    branch_id: str = "main"
    session_id: str = "default_human_session"
    topic: str = "human_peer"
    session_type: str = "Human_to_Human"
