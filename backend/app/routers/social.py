"""REST and SSE endpoints for persisted human-to-human social chat."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import models
from app.crud import chat as chat_crud
from app.crud import group as group_crud
from app.crud import user as user_crud
from app.database import SessionLocal, get_db
from app.schemas.social import (
    SocialContactOut,
    SocialGroupCreate,
    SocialGroupMessageCreate,
    SocialGroupOut,
    SocialMessageCreate,
    SocialMessageOut,
)
from app.security import get_current_user, verify_access_token
from app.services.branching import (
    branch_exists,
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.memory_watcher import (
    check_and_trigger_group_extraction,
    check_and_trigger_h2h_extraction,
)
from app.services.social_realtime import social_realtime_hub


router = APIRouter(tags=["social"])
logger = logging.getLogger(__name__)

SOCIAL_MESSAGE_PAGE_SIZE = 50
SOCIAL_MESSAGE_PAGE_SIZE_MAX = 100
SOCIAL_DIRECTORY_PAGE_SIZE = 100
SOCIAL_DIRECTORY_PAGE_SIZE_MAX = 200
SOCIAL_GROUP_NAME_MEMBER_LIMIT = 4


def _normalize_query_token(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token.partition(" ")[2].strip()
    return token


def _authenticate_query_token(token: str) -> int | None:
    """Validate a query-param bearer token for realtime social events."""
    if not token:
        return None
    try:
        payload = verify_access_token(token)
        user_id = int(payload["sub"])
    except Exception:
        return None

    with SessionLocal() as db:
        user = user_crud.get_user(db, user_id)
        if user is None:
            return None
        return int(user.id)


def _message_out(
    chat_log: models.ChatLog,
    *,
    sender: models.User,
    receiver: models.User,
) -> SocialMessageOut:
    return SocialMessageOut(
        id=int(chat_log.id),
        sender_id=int(sender.id),
        receiver_id=int(receiver.id),
        sender_username=sender.username,
        receiver_username=receiver.username,
        group_id=chat_log.group_id,
        content=str(chat_log.user_message or ""),
        timestamp=chat_log.timestamp,
        is_read=bool(chat_log.is_read),
        branch_id=normalize_branch_id(chat_log.branch_id),
        session_id=chat_log.session_id,
        topic=chat_log.topic,
        session_type=models.SessionType.HUMAN_TO_HUMAN.value,
    )


def _group_message_out(
    chat_log: models.ChatLog,
    *,
    sender: models.User,
    group_id: str,
) -> SocialMessageOut:
    return SocialMessageOut(
        id=int(chat_log.id),
        sender_id=int(sender.id),
        receiver_id=None,
        sender_username=sender.username,
        receiver_username=None,
        group_id=group_id,
        content=str(chat_log.user_message or chat_log.agent_reply or ""),
        timestamp=chat_log.timestamp,
        is_read=True,
        branch_id=normalize_branch_id(chat_log.branch_id),
        session_id=chat_log.session_id,
        topic=chat_log.topic,
        session_type=models.SessionType.GROUP_SHARED.value,
    )


def _message_payload(message: SocialMessageOut) -> dict[str, object]:
    payload = message.model_dump()
    payload["timestamp"] = message.timestamp.isoformat()
    payload["type"] = "social_message"
    return payload


async def _push_social_message_notification(
    *,
    chat_log_id: int,
    sender_user_id: int,
    receiver_user_id: int,
    message_json: str,
) -> None:
    """Fan out a saved social message without holding up the sender response."""
    sse_delivered = await social_realtime_hub.publish(receiver_user_id, message_json)
    logger.info(
        "[Social Chat] message_id=%s sender_id=%s receiver_id=%s sse_delivered=%s",
        chat_log_id,
        sender_user_id,
        receiver_user_id,
        sse_delivered,
    )


def _log_background_task_exception(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("[Social Chat] realtime notification task failed")


async def _push_social_group_message_notification(
    *,
    chat_log_id: int,
    group_id: str,
    receiver_user_ids: list[int],
    message_json: str,
) -> None:
    """Fan out a saved group message over SSE without blocking the sender."""
    sse_delivered_user_ids = await social_realtime_hub.publish_many(
        receiver_user_ids,
        message_json,
    )
    logger.info(
        "[Social Group] message_id=%s group_id=%s sse_delivered=%s",
        chat_log_id,
        group_id,
        sse_delivered_user_ids,
    )


def _log_group_background_task_exception(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("[Social Group] realtime notification task failed")


@router.get("/api/social/events")
async def stream_social_events(
    request: Request,
    token: str = Query(default=""),
) -> StreamingResponse:
    """Stream authenticated social notifications over same-origin HTTP."""
    user_id = _authenticate_query_token(_normalize_query_token(token))
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please sign in again.",
        )

    async def event_stream():
        async for chunk in social_realtime_hub.stream(str(user_id)):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _get_non_admin_user_or_404(db: Session, user_id: int) -> models.User:
    user = user_crud.get_user(db, user_id)
    if user is None or user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found.",
        )
    return user


def _get_group_or_404(db: Session, group_id: str) -> models.Group:
    group = group_crud.get_group(db, group_id)
    if group is None or group.group_type != models.GroupType.HUMAN_ONLY.value:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found.",
        )
    return group


def _get_group_member_user_ids(db: Session, group_id: str) -> list[int]:
    members = group_crud.list_group_members(db, group_id)
    user_ids: list[int] = []
    for member in members:
        if member.entity_type != models.GroupEntityType.USER.value:
            continue
        try:
            user_ids.append(int(member.entity_id))
        except (TypeError, ValueError):
            continue
    return user_ids


def _require_group_member(db: Session, *, group_id: str, user_id: int) -> None:
    if user_id not in _get_group_member_user_ids(db, group_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member of this group.",
        )


def _group_out(
    db: Session,
    group: models.Group,
    branch_id: str = "main",
) -> SocialGroupOut:
    member_ids = _get_group_member_user_ids(db, group.id)
    normalized_branch_id = normalize_branch_id(branch_id)
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    latest_message = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.group_id == group.id,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            models.ChatLog.session_type == models.SessionType.GROUP_SHARED.value,
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .first()
    )
    latest_content = None
    latest_timestamp = None
    if latest_message is not None:
        latest_content = str(
            latest_message.user_message or latest_message.agent_reply or "",
        )
        latest_timestamp = latest_message.timestamp
    return SocialGroupOut(
        id=group.id,
        name=group.name,
        owner_id=group.owner_id,
        member_count=len(member_ids),
        member_ids=member_ids,
        latest_message=latest_content,
        latest_timestamp=latest_timestamp,
    )


def _groups_out(
    db: Session,
    groups: list[models.Group],
    branch_id: str = "main",
) -> list[SocialGroupOut]:
    """Build sidebar group rows with batched member/latest-message lookups."""
    group_ids = [str(group.id) for group in groups]
    if not group_ids:
        return []
    normalized_branch_id = normalize_branch_id(branch_id)
    read_windows = get_branch_read_windows(db, normalized_branch_id)

    member_ids_by_group: dict[str, list[int]] = {group_id: [] for group_id in group_ids}
    member_rows = (
        db.query(models.GroupMember.group_id, models.GroupMember.entity_id)
        .filter(
            models.GroupMember.group_id.in_(group_ids),
            models.GroupMember.entity_type == models.GroupEntityType.USER.value,
        )
        .all()
    )
    for group_id, entity_id in member_rows:
        try:
            member_ids_by_group[str(group_id)].append(int(entity_id))
        except (TypeError, ValueError):
            continue

    ranked_messages = (
        db.query(
            models.ChatLog.group_id.label("group_id"),
            models.ChatLog.user_message.label("user_message"),
            models.ChatLog.agent_reply.label("agent_reply"),
            models.ChatLog.timestamp.label("timestamp"),
            func.row_number()
            .over(
                partition_by=models.ChatLog.group_id,
                order_by=(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc()),
            )
            .label("row_num"),
        )
        .filter(
            models.ChatLog.group_id.in_(group_ids),
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            models.ChatLog.session_type == models.SessionType.GROUP_SHARED.value,
        )
        .subquery()
    )
    latest_rows = (
        db.query(ranked_messages)
        .filter(ranked_messages.c.row_num == 1)
        .all()
    )
    latest_by_group = {
        str(row.group_id): row
        for row in latest_rows
        if row.group_id is not None
    }

    group_rows: list[SocialGroupOut] = []
    for group in groups:
        member_ids = member_ids_by_group.get(str(group.id), [])
        latest_message = latest_by_group.get(str(group.id))
        latest_content = None
        latest_timestamp = None
        if latest_message is not None:
            latest_content = str(
                latest_message.user_message or latest_message.agent_reply or "",
            )
            latest_timestamp = latest_message.timestamp
        group_rows.append(
            SocialGroupOut(
                id=group.id,
                name=group.name,
                owner_id=group.owner_id,
                member_count=len(member_ids),
                member_ids=member_ids,
                latest_message=latest_content,
                latest_timestamp=latest_timestamp,
            ),
        )
    return group_rows


def _default_group_name(users: list[models.User]) -> str:
    names = [user.username for user in users[:SOCIAL_GROUP_NAME_MEMBER_LIMIT]]
    suffix = ""
    if len(users) > SOCIAL_GROUP_NAME_MEMBER_LIMIT:
        suffix = f" +{len(users) - SOCIAL_GROUP_NAME_MEMBER_LIMIT}"
    return "、".join(names)[:120] + suffix


@router.get("/api/social/contacts", response_model=list[SocialContactOut])
def list_social_contacts(
    q: str = Query("", max_length=64),
    branch_id: str = Query("main", min_length=1, max_length=128),
    skip: int = Query(0, ge=0),
    limit: int = Query(
        SOCIAL_DIRECTORY_PAGE_SIZE,
        ge=1,
        le=SOCIAL_DIRECTORY_PAGE_SIZE_MAX,
    ),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[SocialContactOut]:
    """Return a bounded page of non-admin human contacts."""
    clean_query = q.strip()
    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    contacts_query = (
        db.query(models.User)
        .outerjoin(models.Agent, models.Agent.user_id == models.User.id)
        .filter(
            models.User.id != current_user.id,
            models.User.is_admin.is_(False),
            or_(models.Agent.id.is_(None), models.Agent.is_npc.is_(False)),
        )
    )
    if clean_query:
        contacts_query = contacts_query.filter(
            models.User.username.ilike(f"%{clean_query}%"),
        )
    users = (
        contacts_query
        .order_by(models.User.username.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    user_ids = [int(user.id) for user in users]
    if not user_ids:
        return []

    unread_rows = (
        db.query(
            models.ChatLog.sender_user_id,
            func.count(models.ChatLog.id),
        )
        .filter(
            models.ChatLog.session_type == models.SessionType.HUMAN_TO_HUMAN.value,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            models.ChatLog.receiver_user_id == current_user.id,
            models.ChatLog.sender_user_id.in_(user_ids),
            models.ChatLog.is_read.is_(False),
        )
        .group_by(models.ChatLog.sender_user_id)
        .all()
    )
    unread_counts = {
        int(sender_user_id): int(count)
        for sender_user_id, count in unread_rows
        if sender_user_id is not None
    }
    return [
        SocialContactOut(
            user_id=str(user.id),
            username=user.username,
            unread_count=unread_counts.get(int(user.id), 0),
        )
        for user in users
    ]


@router.get(
    "/api/social/messages/{contact_id}",
    response_model=list[SocialMessageOut],
)
def list_social_messages(
    contact_id: int,
    branch_id: str = Query("main", min_length=1, max_length=128),
    skip: int = Query(0, ge=0),
    limit: int = Query(SOCIAL_MESSAGE_PAGE_SIZE, ge=1, le=SOCIAL_MESSAGE_PAGE_SIZE_MAX),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[SocialMessageOut]:
    """Return a bounded page of persisted/offline 1v1 messages."""
    contact = _get_non_admin_user_or_404(db, contact_id)
    if contact.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose another user as the contact.",
        )

    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.session_type == models.SessionType.HUMAN_TO_HUMAN.value,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            or_(
                (
                    (models.ChatLog.sender_user_id == current_user.id)
                    & (models.ChatLog.receiver_user_id == contact.id)
                ),
                (
                    (models.ChatLog.sender_user_id == contact.id)
                    & (models.ChatLog.receiver_user_id == current_user.id)
                ),
            ),
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    rows = list(reversed(rows))

    return [
        _message_out(
            row,
            sender=current_user if row.sender_user_id == current_user.id else contact,
            receiver=contact if row.receiver_user_id == contact.id else current_user,
        )
        for row in rows
    ]


@router.post("/api/social/messages/{contact_id}/read")
def mark_social_messages_read(
    contact_id: int,
    branch_id: str = Query("main", min_length=1, max_length=128),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, int | bool]:
    """Mark all unread incoming 1v1 messages from one contact as read."""
    contact = _get_non_admin_user_or_404(db, contact_id)
    normalized_branch_id = normalize_branch_id(branch_id)
    updated_count = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.session_type == models.SessionType.HUMAN_TO_HUMAN.value,
            models.ChatLog.branch_id == normalized_branch_id,
            models.ChatLog.sender_user_id == contact.id,
            models.ChatLog.receiver_user_id == current_user.id,
            models.ChatLog.is_read.is_(False),
        )
        .update({models.ChatLog.is_read: True}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "updated_count": int(updated_count or 0)}


@router.post(
    "/api/social/messages",
    response_model=SocialMessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_social_message(
    message_in: SocialMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> SocialMessageOut:
    """Persist a 1v1 message, then push a real-time notification if possible."""
    receiver = _get_non_admin_user_or_404(db, message_in.receiver_user_id)
    if receiver.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot send a social message to yourself.",
        )

    normalized_branch_id = normalize_branch_id(message_in.branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    anchor_agent = current_user.agent or receiver.agent
    if anchor_agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one participant must have an Agent before social chat can be logged.",
        )

    chat_log = chat_crud.create_human_to_human_chat_log(
        db,
        sender_user_id=current_user.id,
        receiver_user_id=receiver.id,
        content=message_in.content,
        anchor_agent_id=anchor_agent.id,
        branch_id=normalized_branch_id,
        session_id=message_in.session_id,
        topic=message_in.topic,
    )
    message_out = _message_out(chat_log, sender=current_user, receiver=receiver)

    message_json = json.dumps(_message_payload(message_out), ensure_ascii=False)
    notification_task = asyncio.create_task(
        _push_social_message_notification(
            chat_log_id=chat_log.id,
            sender_user_id=current_user.id,
            receiver_user_id=receiver.id,
            message_json=message_json,
        ),
    )
    notification_task.add_done_callback(_log_background_task_exception)

    check_and_trigger_h2h_extraction(
        str(current_user.id),
        db,
        background_tasks,
        branch_id=normalized_branch_id,
    )
    return message_out


@router.post(
    "/api/social/groups",
    response_model=SocialGroupOut,
    status_code=status.HTTP_201_CREATED,
)
def create_social_group(
    group_in: SocialGroupCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> SocialGroupOut:
    """Create a HUMAN_ONLY IM room and include the creator automatically."""
    member_ids = [current_user.id]
    for contact_id in group_in.contact_ids:
        if contact_id != current_user.id and contact_id not in member_ids:
            member_ids.append(contact_id)

    members = [
        _get_non_admin_user_or_404(db, int(member_id))
        for member_id in member_ids
    ]
    clean_name = group_in.name or _default_group_name(members)
    group = group_crud.create_group(
        db,
        name=clean_name,
        group_type=models.GroupType.HUMAN_ONLY.value,
        owner_id=current_user.id,
        topic="human_room",
    )
    for member in members:
        group_crud.add_group_member(
            group.id,
            str(member.id),
            models.GroupEntityType.USER.value,
            db,
            role="owner" if member.id == current_user.id else "member",
        )
    return _group_out(db, group)


@router.get("/api/social/groups", response_model=list[SocialGroupOut])
def list_social_groups(
    q: str = Query("", max_length=64),
    branch_id: str = Query("main", min_length=1, max_length=128),
    skip: int = Query(0, ge=0),
    limit: int = Query(
        SOCIAL_DIRECTORY_PAGE_SIZE,
        ge=1,
        le=SOCIAL_DIRECTORY_PAGE_SIZE_MAX,
    ),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[SocialGroupOut]:
    """Return a bounded page of HUMAN_ONLY IM rooms for the current user."""
    clean_query = q.strip()
    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    latest_activity = (
        db.query(
            models.ChatLog.group_id.label("group_id"),
            func.max(models.ChatLog.timestamp).label("latest_timestamp"),
        )
        .filter(
            models.ChatLog.session_type == models.SessionType.GROUP_SHARED.value,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
        )
        .group_by(models.ChatLog.group_id)
        .subquery()
    )
    groups_query = (
        db.query(models.Group)
        .join(models.GroupMember, models.GroupMember.group_id == models.Group.id)
        .outerjoin(latest_activity, latest_activity.c.group_id == models.Group.id)
        .filter(
            models.Group.group_type == models.GroupType.HUMAN_ONLY.value,
            models.GroupMember.entity_type == models.GroupEntityType.USER.value,
            models.GroupMember.entity_id == str(current_user.id),
        )
    )
    if clean_query:
        groups_query = groups_query.filter(
            models.Group.name.ilike(f"%{clean_query}%"),
        )
    rows = (
        groups_query
        .order_by(
            latest_activity.c.latest_timestamp.desc().nullslast(),
            models.Group.id.desc(),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    groups = _groups_out(db, rows, normalized_branch_id)
    return groups


@router.get(
    "/api/social/groups/{group_id}/messages",
    response_model=list[SocialMessageOut],
)
def list_social_group_messages(
    group_id: str,
    branch_id: str = Query("main", min_length=1, max_length=128),
    skip: int = Query(0, ge=0),
    limit: int = Query(SOCIAL_MESSAGE_PAGE_SIZE, ge=1, le=SOCIAL_MESSAGE_PAGE_SIZE_MAX),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[SocialMessageOut]:
    """Return a bounded page of persisted group messages."""
    group = _get_group_or_404(db, group_id)
    _require_group_member(db, group_id=group.id, user_id=current_user.id)
    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.group_id == group.id,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            models.ChatLog.session_type == models.SessionType.GROUP_SHARED.value,
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    rows = list(reversed(rows))
    sender_ids = {
        int(row.sender_user_id)
        for row in rows
        if row.sender_user_id is not None
    }
    senders = {
        int(user.id): user
        for user in db.query(models.User).filter(models.User.id.in_(sender_ids)).all()
    }
    return [
        _group_message_out(
            row,
            sender=senders.get(int(row.sender_user_id or 0), current_user),
            group_id=group.id,
        )
        for row in rows
    ]


@router.post(
    "/api/social/groups/{group_id}/messages",
    response_model=SocialMessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_social_group_message(
    group_id: str,
    message_in: SocialGroupMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> SocialMessageOut:
    """Persist one group message, then broadcast it to online room members."""
    group = _get_group_or_404(db, group_id)
    _require_group_member(db, group_id=group.id, user_id=current_user.id)
    normalized_branch_id = normalize_branch_id(message_in.branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    if current_user.agent is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Current user must have an Agent before group messages can be logged.",
        )

    chat_log = chat_crud.create_group_message_log(
        db,
        anchor_agent_id=current_user.agent.id,
        sender_user_id=current_user.id,
        content=message_in.content,
        group_id=group.id,
        branch_id=normalized_branch_id,
        session_id=f"group:{group.id}",
        topic=message_in.topic,
    )
    message_out = _group_message_out(chat_log, sender=current_user, group_id=group.id)
    payload = _message_payload(message_out)
    payload["type"] = "social_group_message"
    message_json = json.dumps(payload, ensure_ascii=False)
    group_member_user_ids = [
        user_id
        for user_id in _get_group_member_user_ids(db, group.id)
        if user_id != current_user.id
    ]
    notification_task = asyncio.create_task(
        _push_social_group_message_notification(
            chat_log_id=chat_log.id,
            group_id=group.id,
            receiver_user_ids=group_member_user_ids,
            message_json=message_json,
        ),
    )
    notification_task.add_done_callback(_log_group_background_task_exception)
    check_and_trigger_group_extraction(
        str(current_user.id),
        db,
        background_tasks,
        branch_id=normalized_branch_id,
    )
    return message_out

