"""Pydantic schemas for virtual agents."""

from pydantic import BaseModel, ConfigDict


class AgentBase(BaseModel):
    """Shared agent fields exposed through the API."""

    agent_name: str
    system_prompt_base: str


class AgentCreate(AgentBase):
    """Fields required to create an agent for a user."""

    user_id: int


class AgentOut(AgentBase):
    """Public agent response model."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
