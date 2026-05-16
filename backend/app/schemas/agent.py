"""Pydantic schemas for virtual agents."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentBase(BaseModel):
    """Shared agent fields exposed through the API."""

    agent_name: str
    system_prompt_base: str
    is_npc: bool = False


class AgentCreate(AgentBase):
    """Fields required to create an agent for a user."""

    user_id: int


class AgentOut(AgentBase):
    """Public agent response model."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int


class AgentDeletionOut(BaseModel):
    """Summary returned after an Agent cascade deletion."""

    agent_id: int
    agent_name: str
    user_id: int
    is_npc: bool
    event_logs_deleted: int
    chat_logs_deleted: int
    vector_memories_deleted: int
    core_memory_cleared: bool
    reflection_events_deleted: int
    relationships_deleted: int
    posts_deleted: int
    feedback_logs_deleted: int
    evaluations_deleted: int
    users_deleted: int
    message: str


class NpcAgentSenderSeedCreate(BaseModel):
    """Incoming sender ids that should have dedicated NPC agents."""

    sender_ids: list[str] = Field(..., min_length=1, max_length=200)

    @field_validator("sender_ids")
    @classmethod
    def normalize_sender_ids(cls, value: list[str]) -> list[str]:
        """Trim, deduplicate, and reject blank sender ids."""
        sender_ids: list[str] = []
        seen: set[str] = set()
        for raw_sender_id in value:
            sender_id = str(raw_sender_id).strip()
            if not sender_id:
                continue
            if sender_id in seen:
                continue
            seen.add(sender_id)
            sender_ids.append(sender_id[:128])
        if not sender_ids:
            raise ValueError("At least one non-empty sender id is required.")
        return sender_ids
