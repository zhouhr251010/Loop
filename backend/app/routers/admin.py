"""High-privilege maintenance endpoints for research data cleanup."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.security import require_admin_key
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    branch_exists,
    normalize_branch_id,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

EVENT_LOG_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS event_logs_no_delete
BEFORE DELETE ON event_logs
BEGIN
    SELECT RAISE(ABORT, 'event_logs are append-only');
END
"""


class BranchPurgeRequest(BaseModel):
    """Incoming payload for purging one non-main world-line branch."""

    branch_id: str = Field(..., min_length=1, max_length=128)


class BranchPurgeResponse(BaseModel):
    """Summary of records removed by a branch purge."""

    branch_id: str
    events_deleted: int
    posts_deleted: int
    chat_logs_deleted: int
    feedback_logs_deleted: int
    post_ids: list[int]
    feedback_log_ids: list[int]
    verification: dict[str, int]
    is_clean: bool
    deletion_log: list[str]
    message: str


def _payload_int(payload: dict[str, Any], key: str) -> int | None:
    raw_value = payload.get(key)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _branch_event_payload_ids(
    events: list[models.EventLog],
) -> tuple[set[int], set[int]]:
    post_ids: set[int] = set()
    feedback_log_ids: set[int] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "POST_CREATED":
            post_id = _payload_int(payload, "post_id")
            if post_id is not None:
                post_ids.add(post_id)
        if event.event_type == "FEEDBACK_CREATED":
            feedback_log_id = _payload_int(payload, "feedback_log_id")
            if feedback_log_id is not None:
                feedback_log_ids.add(feedback_log_id)
    return post_ids, feedback_log_ids


def _count_records_by_ids(
    db: Session,
    model: type[models.Post] | type[models.FeedbackLog],
    ids: set[int],
) -> int:
    if not ids:
        return 0
    return db.query(model).filter(model.id.in_(ids)).count()


@router.post(
    "/purge-branch",
    response_model=BranchPurgeResponse,
)
def purge_branch(
    purge_in: BranchPurgeRequest,
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> BranchPurgeResponse:
    """Hard-delete runtime records for one non-main branch."""
    branch_id = normalize_branch_id(purge_in.branch_id)
    if branch_id == DEFAULT_BRANCH_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The main branch cannot be purged from Lab Console.",
        )
    if not branch_exists(db, branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    branch_events = (
        db.query(models.EventLog)
        .filter(models.EventLog.branch_id == branch_id)
        .all()
    )
    post_ids, feedback_log_ids = _branch_event_payload_ids(branch_events)

    events_matched = len(branch_events)
    posts_matched = _count_records_by_ids(db, models.Post, post_ids)
    chat_logs_matched = (
        db.query(models.ChatLog)
        .filter(models.ChatLog.branch_id == branch_id)
        .count()
    )

    feedback_filters = []
    if feedback_log_ids:
        feedback_filters.append(models.FeedbackLog.id.in_(feedback_log_ids))
    if post_ids:
        feedback_filters.append(models.FeedbackLog.post_id.in_(post_ids))

    feedback_logs_matched = 0
    if feedback_filters:
        feedback_logs_matched = (
            db.query(models.FeedbackLog)
            .filter(or_(*feedback_filters))
            .count()
        )

    feedback_logs_deleted = 0
    if feedback_filters:
        feedback_logs_deleted = (
            db.query(models.FeedbackLog)
            .filter(or_(*feedback_filters))
            .delete(synchronize_session=False)
        )

    posts_deleted = 0
    if post_ids:
        posts_deleted = (
            db.query(models.Post)
            .filter(models.Post.id.in_(post_ids))
            .delete(synchronize_session=False)
        )

    chat_logs_deleted = (
        db.query(models.ChatLog)
        .filter(models.ChatLog.branch_id == branch_id)
        .delete(synchronize_session=False)
    )

    try:
        db.execute(text("DROP TRIGGER IF EXISTS event_logs_no_delete"))
        events_deleted = (
            db.query(models.EventLog)
            .filter(models.EventLog.branch_id == branch_id)
            .delete(synchronize_session=False)
        )
        db.execute(text(EVENT_LOG_DELETE_TRIGGER_SQL))
        db.commit()
    except Exception:
        db.rollback()
        db.execute(text(EVENT_LOG_DELETE_TRIGGER_SQL))
        db.commit()
        raise

    feedback_verification_filters = []
    if feedback_log_ids:
        feedback_verification_filters.append(models.FeedbackLog.id.in_(feedback_log_ids))
    if post_ids:
        feedback_verification_filters.append(models.FeedbackLog.post_id.in_(post_ids))

    remaining_feedback_logs = 0
    if feedback_verification_filters:
        remaining_feedback_logs = (
            db.query(models.FeedbackLog)
            .filter(or_(*feedback_verification_filters))
            .count()
        )
    verification = {
        "remaining_event_logs": (
            db.query(models.EventLog)
            .filter(models.EventLog.branch_id == branch_id)
            .count()
        ),
        "remaining_posts": _count_records_by_ids(db, models.Post, post_ids),
        "remaining_chat_logs": (
            db.query(models.ChatLog)
            .filter(models.ChatLog.branch_id == branch_id)
            .count()
        ),
        "remaining_feedback_logs": remaining_feedback_logs,
    }
    is_clean = all(value == 0 for value in verification.values())
    post_id_list = sorted(post_ids)
    feedback_log_id_list = sorted(feedback_log_ids)
    deletion_log = [
        f"branch_id={branch_id}",
        f"matched event_logs={events_matched}; deleted={events_deleted}; remaining={verification['remaining_event_logs']}",
        f"matched posts={posts_matched}; deleted={posts_deleted}; remaining={verification['remaining_posts']}; post_ids={post_id_list}",
        f"matched chat_logs={chat_logs_matched}; deleted={chat_logs_deleted}; remaining={verification['remaining_chat_logs']}",
        f"matched feedback_logs={feedback_logs_matched}; deleted={feedback_logs_deleted}; remaining={verification['remaining_feedback_logs']}; feedback_log_ids={feedback_log_id_list}",
        f"clean={str(is_clean).lower()}",
    ]

    return BranchPurgeResponse(
        branch_id=branch_id,
        events_deleted=events_deleted,
        posts_deleted=posts_deleted,
        chat_logs_deleted=chat_logs_deleted,
        feedback_logs_deleted=feedback_logs_deleted,
        post_ids=post_id_list,
        feedback_log_ids=feedback_log_id_list,
        verification=verification,
        is_clean=is_clean,
        deletion_log=deletion_log,
        message=(
            f"Purged branch '{branch_id}'. Removed {events_deleted} event(s), "
            f"{posts_deleted} post(s), {chat_logs_deleted} chat log(s), and "
            f"{feedback_logs_deleted} feedback log(s)."
        ),
    )
