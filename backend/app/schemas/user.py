"""Pydantic schemas for users and questionnaire submission."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent import AgentOut


class UserCreate(BaseModel):
    """Incoming payload for user registration."""

    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        """Trim surrounding whitespace before storage."""
        return str(value).strip()

    @field_validator("password")
    @classmethod
    def validate_bcrypt_password_size(cls, value: str) -> str:
        """Reject passwords bcrypt cannot hash without truncation."""
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 UTF-8 bytes or fewer.")
        return value


class UserLogin(BaseModel):
    """Incoming payload for user login."""

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        """Trim surrounding whitespace before lookup."""
        return str(value).strip()

    @field_validator("password")
    @classmethod
    def validate_bcrypt_password_size(cls, value: str) -> str:
        """Reject passwords bcrypt cannot verify without truncation."""
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 UTF-8 bytes or fewer.")
        return value


class UserOut(BaseModel):
    """Public user response model. Never includes password hashes."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    mbti_type: str | None = None
    big_five_scores: dict[str, Any] | None = None
    schwartz_values: dict[str, Any] | None = None
    autobiography: str | None = None
    core_memory: dict[str, Any] | None = None


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


class AuthSessionOut(BaseModel):
    """Authenticated session returned after register/login."""

    user: UserOut
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AgentSessionChoiceOut(BaseModel):
    """Research/admin view of an existing user-Agent pair."""

    user: UserOut
    agent: AgentOut


class NpcAgentSenderSeedOut(BaseModel):
    """NPC agent created or reused for one imported sender id."""

    sender_id: str
    user: UserOut
    agent: AgentOut
