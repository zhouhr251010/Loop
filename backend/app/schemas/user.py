"""Pydantic schemas for users and questionnaire submission."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .agent import AgentOut


class UserCreate(BaseModel):
    """Incoming payload for user registration."""

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)


class UserOut(BaseModel):
    """Public user response model. Never includes password hashes."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    mbti_type: str | None = None
    big_five_scores: dict[str, Any] | None = None
    schwartz_values: dict[str, Any] | None = None
    autobiography: str | None = None


class QuestionnaireCreate(BaseModel):
    """Incoming payload for personality and values questionnaire data."""

    mbti_type: str = Field(..., min_length=2, max_length=16)
    big_five_scores: dict[str, Any]
    schwartz_values: dict[str, Any]
    autobiography: str | None = Field(default=None, max_length=8000)


class QuestionnaireSubmissionOut(BaseModel):
    """Response returned after questionnaire data creates or updates an agent."""

    user: UserOut
    agent: AgentOut
