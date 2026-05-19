"""Pydantic schemas for supervised multi-agent debates."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class DebateTriggerRequest(BaseModel):
    """Admin request to run one branch-bound supervised debate."""

    topic: str = Field(..., min_length=1, max_length=500)
    participant_agent_ids: list[str] = Field(..., min_length=1, max_length=20)
    branch_id: str = Field(..., min_length=1, max_length=128)
    max_turns: int = Field(10, ge=1, le=20)

    @field_validator("topic", "branch_id", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized

    @field_validator("participant_agent_ids")
    @classmethod
    def normalize_participant_agent_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw_agent_id in value:
            agent_id = str(raw_agent_id or "").strip()
            if not agent_id:
                raise ValueError("Participant Agent IDs must not be blank.")
            try:
                parsed_agent_id = int(agent_id)
            except ValueError as exc:
                raise ValueError("Participant Agent IDs must be integers.") from exc
            if parsed_agent_id <= 0:
                raise ValueError("Participant Agent IDs must be positive integers.")
            canonical_id = str(parsed_agent_id)
            if canonical_id not in normalized:
                normalized.append(canonical_id)
        if not normalized:
            raise ValueError("At least one participant Agent is required.")
        return normalized


class DebateTriggerResponse(BaseModel):
    """Result returned after a supervised debate completes."""

    status: str
    turns_executed: int
    consensus_reached: bool
    final_report: dict[str, Any] | str
