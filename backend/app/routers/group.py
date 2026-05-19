"""REST endpoints for Boundary-1-isolated N-to-N chat groups."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app import models
from app.crud import group as group_crud
from app.crud.chat import create_group_message_log
from app.database import SessionLocal, get_db
from app.security import get_current_user
from app.services.branching import branch_exists, normalize_branch_id
from app.services.rolling_summary import (
    collect_group_messages_for_summary,
    update_group_summary_background,
)


router = APIRouter(prefix="/api/groups", tags=["groups"])

GroupTypeIn = Literal["HUMAN_ONLY", "AGENT_ONLY"]
GroupEntityTypeIn = Literal["USER", "AGENT"]


class GroupCreate(BaseModel):
    """Request body for group creation."""

    name: str = Field(..., min_length=1, max_length=128)
    topic: str | None = Field(None, max_length=255)
    group_type: GroupTypeIn

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Group name must not be blank.")
        return normalized

    @field_validator("topic", mode="before")
    @classmethod
    def normalize_topic(cls, value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class GroupMemberCreate(BaseModel):
    """Request body for adding a member to a group."""

    entity_id: str = Field(..., min_length=1, max_length=128)
    entity_type: GroupEntityTypeIn
    role: str = Field("member", min_length=1, max_length=32)


class GroupMessageCreate(BaseModel):
    """Request body for human messages in HUMAN_ONLY groups."""

    content: str = Field(..., min_length=1, max_length=4000)
    branch_id: str = Field(..., min_length=1, max_length=128)
    topic: str = Field("group_chat", min_length=1, max_length=64)

    @field_validator("content", "branch_id", "topic", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized


class GroupOut(BaseModel):
    """Response model for a group."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    topic: str | None = None
    group_type: str


class GroupMemberOut(BaseModel):
    """Response model for a group member."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: str
    entity_id: str
    entity_type: str
    role: str


class GroupMessageOut(BaseModel):
    """Response model for one persisted group message."""

    id: int
    group_id: str
    sender_user_id: int
    content: str
    branch_id: str
    session_type: str
    timestamp: datetime


def _http_from_value_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    if "not found" in message.lower():
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=message,
    )


def _user_is_group_member(db: Session, *, group_id: str, user_id: int) -> bool:
    return (
        db.query(models.GroupMember.id)
        .filter(
            models.GroupMember.group_id == group_id,
            models.GroupMember.entity_id == str(user_id),
            models.GroupMember.entity_type == models.GroupEntityType.USER.value,
        )
        .first()
        is not None
    )


def _schedule_summary_if_needed(
    group_id: str,
    branch_id: str,
    db: Session,
    background_tasks: BackgroundTasks,
) -> None:
    ejected_messages = collect_group_messages_for_summary(group_id, db, branch_id)
    if ejected_messages:
        background_tasks.add_task(
            update_group_summary_background,
            group_id,
            ejected_messages,
            SessionLocal,
            branch_id,
        )


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    group_in: GroupCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
) -> models.Group:
    """Create a human-only or agent-only group."""
    try:
        return group_crud.create_group(
            db,
            name=group_in.name,
            topic=group_in.topic,
            group_type=group_in.group_type,
            owner_id=_current_user.id,
        )
    except ValueError as exc:
        raise _http_from_value_error(exc) from exc


@router.post(
    "/{group_id}/members",
    response_model=GroupMemberOut,
    status_code=status.HTTP_201_CREATED,
)
def add_group_member(
    group_id: str,
    member_in: GroupMemberCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
) -> models.GroupMember:
    """Add a group member using the Boundary 1 guard in CRUD."""
    try:
        return group_crud.add_group_member(
            group_id,
            member_in.entity_id,
            member_in.entity_type,
            db,
            role=member_in.role,
        )
    except ValueError as exc:
        raise _http_from_value_error(exc) from exc


@router.post(
    "/{group_id}/messages",
    response_model=GroupMessageOut,
    status_code=status.HTTP_201_CREATED,
)
def post_human_group_message(
    group_id: str,
    message_in: GroupMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> GroupMessageOut:
    """Persist one human message in a HUMAN_ONLY group."""
    group = group_crud.get_group(db, group_id)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found.",
        )
    if group.group_type != models.GroupType.HUMAN_ONLY.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Human messages can only be posted to HUMAN_ONLY groups.",
        )

    normalized_branch_id = normalize_branch_id(message_in.branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    if not _user_is_group_member(db, group_id=group.id, user_id=current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a USER member of this group to post messages.",
        )
    if current_user.agent is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Current user must have an Agent before group messages can be logged.",
        )

    try:
        chat_log = create_group_message_log(
            db,
            anchor_agent_id=current_user.agent.id,
            sender_user_id=current_user.id,
            content=message_in.content,
            group_id=group.id,
            branch_id=normalized_branch_id,
            session_id=f"group:{group.id}",
            topic=message_in.topic or group.topic or "group_chat",
        )
    except ValueError as exc:
        raise _http_from_value_error(exc) from exc

    _schedule_summary_if_needed(group.id, normalized_branch_id, db, background_tasks)
    return GroupMessageOut(
        id=chat_log.id,
        group_id=group.id,
        sender_user_id=current_user.id,
        content=message_in.content,
        branch_id=normalized_branch_id,
        session_type=models.SessionType.GROUP_SHARED.value,
        timestamp=chat_log.timestamp,
    )
