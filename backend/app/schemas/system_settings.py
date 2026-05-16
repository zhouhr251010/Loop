"""Pydantic schemas for global experiment settings."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SystemSettingsOut(BaseModel):
    """Public view of the global experiment controls."""

    model_config = ConfigDict(from_attributes=True)

    allow_user_branch_switch: bool = False
    global_active_branch: str = "main"
    updated_at: datetime | None = None


class SystemSettingsPatch(BaseModel):
    """Admin update payload for global experiment controls."""

    allow_user_branch_switch: bool | None = None
    global_active_branch: str | None = Field(default=None, min_length=1, max_length=128)
