"""Pydantic schemas for daily private sync chats."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChatModelChoice = Literal["fast", "deep"]


class ChatMessageCreate(BaseModel):
    """Incoming private chat message from the user."""

    message: str = Field(..., min_length=1, max_length=4000)
    branch_id: str = Field(..., min_length=1, max_length=128)
    model: ChatModelChoice = "fast"


class ChatLogOut(BaseModel):
    """Stored chat log response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    user_message: str
    agent_reply: str
    timestamp: datetime
    branch_id: str = "main"


class ChatReplyOut(BaseModel):
    """Response returned after a private sync message."""

    reply: str
    chat_log: ChatLogOut | None = None
    memory_chunks_used: int = 0
    model_used: ChatModelChoice = "fast"
    stored: bool = True
    warning: str | None = None
