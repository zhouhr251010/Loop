"""RESTful endpoints for public-square posts and correction feedback."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models
from app.crud import agent as agent_crud
from app.crud import feedback as feedback_crud
from app.crud import post as post_crud
from app.database import get_db
from app.schemas.feedback import FeedbackCreate, FeedbackLogOut
from app.schemas.post import PostCreate, PostFeedOut, PostOut
from app.security import get_current_user
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    branch_exists,
    get_branch_anchor,
    normalize_branch_id,
)
from app.services.feedback_service import reflect_and_merge_feedback


router = APIRouter(tags=["posts"])


def _require_current_agent(db: Session, current_user: models.User) -> models.Agent:
    db_agent = agent_crud.get_agent_by_user_id(db, current_user.id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found for this user.",
        )
    return db_agent


def _payload_post_id(payload: dict[str, Any]) -> int | None:
    raw_post_id = payload.get("post_id")
    try:
        return int(raw_post_id)
    except (TypeError, ValueError):
        return None


def _post_out(post: models.Post, branch_id: str) -> PostOut:
    return PostOut(
        id=post.id,
        agent_id=post.agent_id,
        content=post.content,
        timestamp=post.timestamp,
        branch_id=normalize_branch_id(branch_id),
    )


def _collect_plaza_events(
    db: Session,
    branch_id: str,
    event_type: str,
    until: datetime | None = None,
    visited_branches: set[str] | None = None,
) -> list[models.EventLog]:
    """Return inherited global-world plaza events for one branch."""
    normalized_branch_id = normalize_branch_id(branch_id)
    visited = set(visited_branches or set())
    if normalized_branch_id in visited:
        return []
    visited.add(normalized_branch_id)

    anchor = get_branch_anchor(db, normalized_branch_id)
    inherited_events: list[models.EventLog] = []
    lower_bound: datetime | None = None
    if anchor is not None:
        inherited_events = _collect_plaza_events(
            db,
            anchor.parent_branch_id,
            event_type,
            until=anchor.fork_timestamp,
            visited_branches=visited,
        )
        lower_bound = anchor.fork_timestamp

    filters = [
        models.EventLog.branch_id == normalized_branch_id,
        models.EventLog.event_type == event_type,
    ]
    if until is not None:
        filters.append(models.EventLog.timestamp < until)
    if lower_bound is not None:
        filters.append(models.EventLog.timestamp >= lower_bound)

    branch_events = (
        db.query(models.EventLog)
        .filter(*filters)
        .order_by(models.EventLog.timestamp.asc(), models.EventLog.event_id.asc())
        .all()
    )
    return [*inherited_events, *branch_events]


def _latest_feedback_corrections(
    db: Session,
    branch_id: str,
) -> dict[int, str]:
    """Return latest corrected display text per post for a branch projection."""
    corrections: dict[int, str] = {}
    for event in _collect_plaza_events(db, branch_id, "FEEDBACK_CREATED"):
        payload = event.payload if isinstance(event.payload, dict) else {}
        post_id = _payload_post_id(payload)
        corrected_text = str(payload.get("corrected_text") or "").strip()
        if post_id is None or not corrected_text:
            continue
        corrections[post_id] = corrected_text
    return corrections


def _branch_lineage_ids(
    db: Session,
    branch_id: str,
    visited_branches: set[str] | None = None,
) -> set[str]:
    """Return the selected branch and only its parent branch chain."""
    normalized_branch_id = normalize_branch_id(branch_id)
    visited = set(visited_branches or set())
    if normalized_branch_id in visited:
        return visited

    visited.add(normalized_branch_id)
    anchor = get_branch_anchor(db, normalized_branch_id)
    if anchor is not None:
        return _branch_lineage_ids(db, anchor.parent_branch_id, visited)
    return visited


def _plaza_feed_for_branch(
    db: Session,
    branch_id: str,
    skip: int,
    limit: int,
) -> list[PostFeedOut]:
    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    events = _collect_plaza_events(db, normalized_branch_id, "POST_CREATED")
    corrected_text_by_post_id = _latest_feedback_corrections(db, normalized_branch_id)
    allowed_branch_ids = _branch_lineage_ids(db, normalized_branch_id)
    events = [
        event
        for event in events
        if normalize_branch_id(event.branch_id) in allowed_branch_ids
    ]
    unique_events: dict[int, models.EventLog] = {
        event.event_id: event for event in events
    }
    sorted_events = sorted(
        unique_events.values(),
        key=lambda event: (event.timestamp, event.event_id),
        reverse=True,
    )

    feed: list[PostFeedOut] = []
    for event in sorted_events[skip : skip + limit]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        post_id = _payload_post_id(payload)
        if post_id is None:
            continue
        content = corrected_text_by_post_id.get(post_id, "").strip()
        is_corrected = bool(content)
        if not content:
            content = str(payload.get("content") or "").strip()
        if not content:
            db_post = post_crud.get_post(db, post_id)
            content = db_post.content if db_post is not None else ""
        if not content:
            continue
        feed.append(
            PostFeedOut(
                id=post_id,
                agent_id=event.agent_id,
                agent_name=event.agent.agent_name,
                content=content,
                timestamp=event.timestamp,
                branch_id=event.branch_id,
                is_corrected=is_corrected,
            ),
        )
    return feed


@router.post(
    "/api/agents/me/posts",
    response_model=PostOut,
    status_code=status.HTTP_201_CREATED,
)
def create_my_agent_post(
    post_in: PostCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> PostOut:
    """Create a public-square post for the authenticated user's Agent."""
    db_agent = _require_current_agent(db, current_user)
    return _post_out(
        post_crud.create_post(db, db_agent.id, post_in),
        post_in.branch_id,
    )


@router.post(
    "/api/agents/{agent_id}/posts",
    response_model=PostOut,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_post(
    agent_id: int,
    post_in: PostCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> PostOut:
    """Create a simulated post from an agent."""
    db_agent = db.get(models.Agent, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only create posts for your own agent.",
        )

    return _post_out(post_crud.create_post(db, agent_id, post_in), post_in.branch_id)


@router.get("/api/posts", response_model=list[PostFeedOut])
def get_public_feed(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    branch_id: str = DEFAULT_BRANCH_ID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[PostFeedOut]:
    """Return the public-square feed, newest posts first."""
    return _plaza_feed_for_branch(db, branch_id, skip, limit)


@router.get("/api/plaza/events", response_model=list[PostFeedOut])
def get_plaza_events(
    branch_id: str = DEFAULT_BRANCH_ID,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[PostFeedOut]:
    """Return inherited public plaza events for one global world-line."""
    return _plaza_feed_for_branch(db, branch_id, skip, limit)


@router.post(
    "/api/posts/{post_id}/feedback",
    response_model=FeedbackLogOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_post_feedback(
    post_id: int,
    feedback_in: FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> FeedbackLogOut:
    """Record user correction feedback for a post from the user's own agent."""
    branch_id = normalize_branch_id(feedback_in.branch_id)
    if not branch_exists(db, branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    db_post = post_crud.get_post(db, post_id)
    if db_post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found.",
        )

    if feedback_in.user_id is not None and feedback_in.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Feedback user_id must match the authenticated user.",
        )

    if db_post.agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Users can only correct posts generated by their own agent.",
        )

    feedback_in.user_id = current_user.id
    feedback_in.branch_id = branch_id
    feedback_log = feedback_crud.create_feedback_log(db, db_post, feedback_in)
    try:
        await reflect_and_merge_feedback(
            db,
            feedback_log=feedback_log,
            post=db_post,
            branch_id=branch_id,
        )
    except Exception as exc:
        print(
            (
                "[Loop Feedback Reflection] failed: "
                f"{exc.__class__.__name__}: {exc}"
            ),
            flush=True,
        )
    return feedback_log
