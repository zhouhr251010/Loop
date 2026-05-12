"""Pydantic schemas for digital memory uploads."""

from typing import Any

from pydantic import BaseModel, Field


class MemoryUploadCreate(BaseModel):
    """Incoming long-form memory content from a user."""

    content: str = Field(..., min_length=1, max_length=50000)


class MemoryUploadOut(BaseModel):
    """Response returned after memory chunks are stored."""

    message: str
    chunks_added: int


class MemorySearchCreate(BaseModel):
    """Incoming payload for user-scoped memory retrieval tests."""

    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)


class MemorySearchOut(BaseModel):
    """Response returned after retrieving relevant memory chunks."""

    query: str
    chunks: list[str]


class RelationshipChangeOut(BaseModel):
    """One social-affinity update inferred during memory consolidation."""

    target_agent_id: int
    affinity_change: float
    affinity_score: float


class MemoryConsolidationOut(BaseModel):
    """Response returned after an agent sleep/consolidation cycle."""

    message: str
    user_id: int
    agent_id: int
    records_consolidated: int
    chunks_added: int
    graph_triples_extracted: int = 0
    daily_events_created: int = 0
    high_level_insights_created: int = 0
    core_memory_updated: bool = False
    relationship_updates: list[RelationshipChangeOut]
    graph_memory_cleared: bool


class AgentWorkingMemoryOut(BaseModel):
    """Inspectable short-term LangGraph memory state for one Agent."""

    agent_id: int
    branch_id: str = "main"
    graph_available: bool
    message_count: int = 0
    working_message_count: int = 0
    core_memory: dict[str, Any] = Field(default_factory=dict)
    current_core_memory: str = ""
    summary: str = ""
    active_topic: str = ""
    topic_count: int = 0
    topic_message_counts: dict[str, int] = Field(default_factory=dict)
    topic_summaries: dict[str, str] = Field(default_factory=dict)
    emotion: str = "平静"
    energy: int = 100
    error: str | None = None


class RelationshipOut(BaseModel):
    """Directed social-affinity score from the current Agent to another Agent."""

    target_agent_id: int
    target_agent_name: str
    affinity_score: float


class PersonalizedPostPreviewOut(BaseModel):
    """Post ordering preview for filter-bubble/social-graph experiments."""

    id: int
    agent_id: int
    agent_name: str
    affinity_score: float
    content: str
    timestamp: str
