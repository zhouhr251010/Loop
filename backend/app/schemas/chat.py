"""Pydantic schemas for daily private sync chats."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageCreate(BaseModel):
    """Incoming private chat message from the user."""

    message: str = Field(..., min_length=1, max_length=4000)


class ChatLogOut(BaseModel):
    """Stored chat log response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    user_message: str
    agent_reply: str
    timestamp: datetime


class ChatReplyOut(BaseModel):
    """Response returned after a private sync message."""

    reply: str
    chat_log: ChatLogOut
    memory_chunks_used: int = 0
