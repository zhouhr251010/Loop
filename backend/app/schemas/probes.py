"""Pydantic schemas for baseline and counterfactual probe collection."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProbeSubmitRequest(BaseModel):
    """One human or agent answer to a validation probe item."""

    probe_set: str = Field(..., min_length=1, max_length=64)
    probe_id: str = Field(..., min_length=1, max_length=128)
    answer: dict[str, Any]


class ProbeSubmitResponse(BaseModel):
    """Summary returned after storing probe responses."""

    submitted: int


class ProbeStatusResponse(BaseModel):
    """Weekly baseline-probe reminder status for one authenticated user."""

    needs_update: bool
    last_submitted: datetime | None = None
