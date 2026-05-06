"""Pydantic schemas for digital memory uploads."""

from pydantic import BaseModel, Field


class MemoryUploadCreate(BaseModel):
    """Incoming long-form memory content from a user."""

    content: str = Field(..., min_length=1, max_length=50000)


class MemoryUploadOut(BaseModel):
    """Response returned after memory chunks are stored."""

    message: str
    chunks_added: int
