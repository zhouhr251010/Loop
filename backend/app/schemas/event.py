"""Pydantic schemas for event-sourced simulation timelines."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventLogOut(BaseModel):
    """Public response model for an immutable event-store record."""

    model_config = ConfigDict(from_attributes=True)

    event_id: int
    timestamp: datetime
    agent_id: int
    branch_id: str
    event_type: str
    payload: dict[str, Any]


class AgentStateOut(BaseModel):
    """In-memory state reconstructed by replaying an agent timeline."""

    agent_id: int
    branch_id: str
    target_timestamp: datetime
    core_memory: dict[str, Any]
    current_core_memory: str = ""
    working_memory: dict[str, Any]
    intimacy: dict[str, float]
    replayed_events: int


class SimulationForkCreate(BaseModel):
    """Incoming payload for creating a counterfactual timeline branch."""

    agent_id: int = Field(..., gt=0)
    source_branch_id: str = Field(default="main", min_length=1, max_length=128)
    source_event_id: int | None = Field(default=None, gt=0)
    rollback_timestamp: datetime
    new_branch_name: str = Field(..., min_length=1, max_length=128)
    counterfactual_event: dict[str, Any] = Field(default_factory=dict)


class SimulationForkOut(BaseModel):
    """Response returned after a new counterfactual branch is created."""

    branch_id: str
    rollback_timestamp: datetime
    injected_event: EventLogOut
    reconstructed_state: AgentStateOut
