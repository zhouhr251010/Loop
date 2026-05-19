"""Database operations for private agent chat logs."""

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.event_store import append_event

RECENT_CHAT_HISTORY_TURNS = 30
HISTORICAL_CHAT_LOG_MIN_TURNS = 5
HISTORICAL_CHAT_LOG_MAX_TURNS = 50
MIN_SOCIAL_MEMORY_SIGNAL_CHARS = 5


def _chat_replay_sort_key(
    row: models.ChatLog,
    branch_rank: dict[str, int] | None = None,
) -> tuple[object, int, int]:
    normalized_branch_id = normalize_branch_id(row.branch_id)
    fallback_rank = 0 if normalized_branch_id == DEFAULT_BRANCH_ID else 1
    return (
        row.timestamp,
        (branch_rank or {}).get(normalized_branch_id, fallback_rank),
        int(row.id or 0),
    )


def create_chat_log(
    db: Session,
    agent_id: int,
    user_message: str,
    agent_reply: str,
    branch_id: str = "main",
    session_id: str = "default_session",
    experiment_mode: str = "mode_alpha",
    topic: str = "general",
    session_type: str = models.SessionType.HUMAN_TO_AGENT.value,
) -> models.ChatLog:
    """Persist a user-agent private chat turn with second-level precision."""
    normalized_branch_id = normalize_branch_id(branch_id)
    normalized_session_id = (
        (session_id or "default_session").strip() or "default_session"
    )
    normalized_experiment_mode = (
        (experiment_mode or "mode_alpha").strip() or "mode_alpha"
    )
    allowed_session_types = {item.value for item in models.SessionType}
    normalized_session_type = (
        (session_type or models.SessionType.HUMAN_TO_AGENT.value).strip()
        or models.SessionType.HUMAN_TO_AGENT.value
    )
    if normalized_session_type not in allowed_session_types:
        normalized_session_type = models.SessionType.HUMAN_TO_AGENT.value
    normalized_topic = (topic or "general").strip()[:64] or "general"
    timestamp = utc_now_seconds()
    db_chat_log = models.ChatLog(
        agent_id=agent_id,
        branch_id=normalized_branch_id,
        session_id=normalized_session_id,
        experiment_mode=normalized_experiment_mode,
        session_type=normalized_session_type,
        is_memory_extracted=(
            normalized_session_type != models.SessionType.HUMAN_TO_HUMAN.value
        ),
        topic=normalized_topic,
        user_message=user_message,
        agent_reply=agent_reply,
        timestamp=timestamp,
    )
    db.add(db_chat_log)
    db.flush()
    append_event(
        db,
        agent_id=agent_id,
        branch_id=normalized_branch_id,
        event_type="MESSAGE_RECEIVED",
        payload={
            "user_message": user_message,
            "agent_reply": agent_reply,
            "chat_log_id": db_chat_log.id,
            "session_id": normalized_session_id,
            "experiment_mode": normalized_experiment_mode,
            "session_type": normalized_session_type,
            "topic": normalized_topic,
        },
        timestamp=timestamp,
        commit=False,
    )
    db.commit()
    db.refresh(db_chat_log)
    return db_chat_log


def create_human_to_human_chat_log(
    db: Session,
    *,
    sender_user_id: int,
    receiver_user_id: int,
    content: str,
    anchor_agent_id: int,
    branch_id: str,
    session_id: str = "default_human_session",
    topic: str = "human_peer",
) -> models.ChatLog:
    """Persist one human-to-human message and append its timeline event."""
    normalized_branch_id = normalize_branch_id(branch_id)
    normalized_session_id = (
        (session_id or "default_human_session").strip()[:64]
        or "default_human_session"
    )
    normalized_topic = (topic or "human_peer").strip()[:64] or "human_peer"
    clean_content = (content or "").strip()
    is_low_signal_message = len(clean_content) < MIN_SOCIAL_MEMORY_SIGNAL_CHARS
    timestamp = utc_now_seconds()
    db_chat_log = models.ChatLog(
        agent_id=anchor_agent_id,
        sender_user_id=sender_user_id,
        receiver_user_id=receiver_user_id,
        branch_id=normalized_branch_id,
        session_id=normalized_session_id,
        experiment_mode="mode_alpha",
        session_type=models.SessionType.HUMAN_TO_HUMAN.value,
        is_memory_extracted=is_low_signal_message,
        is_read=False,
        topic=normalized_topic,
        user_message=clean_content,
        agent_reply="",
        timestamp=timestamp,
    )
    db.add(db_chat_log)
    db.flush()
    append_event(
        db,
        agent_id=anchor_agent_id,
        branch_id=normalized_branch_id,
        event_type="HUMAN_MESSAGE_RECEIVED",
        payload={
            "chat_log_id": db_chat_log.id,
            "sender_user_id": sender_user_id,
            "receiver_user_id": receiver_user_id,
            "content": clean_content,
            "session_id": normalized_session_id,
            "session_type": models.SessionType.HUMAN_TO_HUMAN.value,
            "topic": normalized_topic,
        },
        timestamp=timestamp,
        commit=False,
    )
    db.commit()
    db.refresh(db_chat_log)
    return db_chat_log


def create_group_message_log(
    db: Session,
    *,
    anchor_agent_id: int,
    content: str,
    group_id: str,
    branch_id: str,
    sender_user_id: int | None = None,
    speaker_agent_id: int | None = None,
    session_id: str = "default_group_session",
    topic: str = "group_chat",
) -> models.ChatLog:
    """Persist one N-to-N group message and append its timeline event."""
    normalized_group_id = str(group_id or "").strip()
    if not normalized_group_id:
        raise ValueError("group_id must not be blank.")
    normalized_branch_id = normalize_branch_id(branch_id)
    normalized_session_id = (
        (session_id or "default_group_session").strip()[:64]
        or "default_group_session"
    )
    normalized_topic = (topic or "group_chat").strip()[:64] or "group_chat"
    clean_content = (content or "").strip()
    if not clean_content:
        raise ValueError("Group message content must not be blank.")

    timestamp = utc_now_seconds()
    is_agent_message = speaker_agent_id is not None
    is_low_signal_message = len(clean_content) < MIN_SOCIAL_MEMORY_SIGNAL_CHARS
    db_chat_log = models.ChatLog(
        agent_id=anchor_agent_id,
        sender_user_id=sender_user_id,
        receiver_user_id=None,
        group_id=normalized_group_id,
        branch_id=normalized_branch_id,
        session_id=normalized_session_id,
        experiment_mode="mode_alpha",
        session_type=models.SessionType.GROUP_SHARED.value,
        is_memory_extracted=is_agent_message or is_low_signal_message,
        topic=normalized_topic,
        user_message="" if is_agent_message else clean_content,
        agent_reply=clean_content if is_agent_message else "",
        timestamp=timestamp,
    )
    db.add(db_chat_log)
    db.flush()
    append_event(
        db,
        agent_id=anchor_agent_id,
        branch_id=normalized_branch_id,
        event_type="GROUP_MESSAGE_RECEIVED",
        payload={
            "chat_log_id": db_chat_log.id,
            "group_id": normalized_group_id,
            "sender_user_id": sender_user_id,
            "speaker_agent_id": speaker_agent_id,
            "content": clean_content,
            "session_id": normalized_session_id,
            "session_type": models.SessionType.GROUP_SHARED.value,
            "topic": normalized_topic,
        },
        timestamp=timestamp,
        commit=False,
    )
    db.commit()
    db.refresh(db_chat_log)
    return db_chat_log


def get_recent_chat_logs(
    db: Session,
    agent_id: int,
    branch_id: str = "main",
    session_id: str = "default_session",
    topic: str | None = None,
    limit: int = RECENT_CHAT_HISTORY_TURNS,
) -> list[models.ChatLog]:
    """Return a bounded recent private-chat window in chronological order."""
    normalized_branch_id = (branch_id or "main").strip() or "main"
    normalized_session_id = (
        (session_id or "default_session").strip() or "default_session"
    )
    normalized_topic = (topic or "general").strip()[:64] or "general"
    safe_limit = max(1, min(limit, RECENT_CHAT_HISTORY_TURNS))
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    filters = [
        models.ChatLog.agent_id == agent_id,
        branch_window_filter(
            models.ChatLog.branch_id,
            models.ChatLog.timestamp,
            None,
            read_windows,
        ),
        models.ChatLog.session_id == normalized_session_id,
    ]
    if topic is not None:
        filters.append(models.ChatLog.topic == normalized_topic)
    rows = (
        db.query(models.ChatLog)
        .filter(*filters)
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .limit(safe_limit)
        .all()
    )
    branch_rank = {
        window.branch_id: index
        for index, window in enumerate(read_windows)
    }
    return sorted(rows, key=lambda row: _chat_replay_sort_key(row, branch_rank))


def get_historical_chat_logs(
    db: Session,
    agent_id: int,
    branch_id: str = "main",
    session_id: str = "default_session",
    topic: str | None = None,
    lookback_turns: int = HISTORICAL_CHAT_LOG_MIN_TURNS,
    skip_recent_turns: int = RECENT_CHAT_HISTORY_TURNS,
) -> list[models.ChatLog]:
    """Return older branch-scoped chat turns for model tool lookups."""
    normalized_branch_id = normalize_branch_id(branch_id)
    normalized_session_id = (
        (session_id or "default_session").strip() or "default_session"
    )
    normalized_topic = (topic or "general").strip()[:64] or "general"
    safe_lookback = max(
        HISTORICAL_CHAT_LOG_MIN_TURNS,
        min(lookback_turns, HISTORICAL_CHAT_LOG_MAX_TURNS),
    )
    safe_skip = max(0, skip_recent_turns)
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    filters = [
        models.ChatLog.agent_id == agent_id,
        branch_window_filter(
            models.ChatLog.branch_id,
            models.ChatLog.timestamp,
            None,
            read_windows,
        ),
        models.ChatLog.session_id == normalized_session_id,
    ]
    if topic is not None:
        filters.append(models.ChatLog.topic == normalized_topic)
    rows = (
        db.query(models.ChatLog)
        .filter(*filters)
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .offset(safe_skip)
        .limit(safe_lookback)
        .all()
    )
    branch_rank = {
        window.branch_id: index
        for index, window in enumerate(read_windows)
    }
    return sorted(rows, key=lambda row: _chat_replay_sort_key(row, branch_rank))
