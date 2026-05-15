"""Pydantic schemas for public blind-test evaluations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvaluatorRelation = Literal["朋友", "同事", "伴侣", "亲属", "其他"]


class BlindTestChatLogOut(BaseModel):
    """One sampled chat turn shown to an external evaluator."""

    id: int
    user_message: str
    agent_reply: str
    timestamp: datetime


class BlindTestOut(BaseModel):
    """Public blind-test payload for one Agent."""

    agent_id: int
    agent_name: str
    samples: list[BlindTestChatLogOut]


class BlindTestSubmitCreate(BaseModel):
    """External evaluator authenticity rating."""

    evaluator_relation: EvaluatorRelation
    authenticity_score: int = Field(..., ge=1, le=5)
    qualitative_feedback: str = Field("", max_length=4000)
    sampled_chat_log_ids: list[int] = Field(default_factory=list, max_length=20)


class BlindTestSubmitOut(BaseModel):
    """Stored blind-test evaluation response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    evaluator_relation: str
    authenticity_score: int
    qualitative_feedback: str | None = None
    sampled_chat_log_ids: list[int] | None = None
    timestamp: datetime
