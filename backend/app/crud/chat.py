"""Database operations for private agent chat logs."""

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.services.branching import DEFAULT_BRANCH_ID, branch_scope_ids, normalize_branch_id
from app.services.event_store import append_event

RECENT_CHAT_HISTORY_TURNS = 30
HISTORICAL_CHAT_LOG_MIN_TURNS = 5
HISTORICAL_CHAT_LOG_MAX_TURNS = 50


def _chat_replay_sort_key(row: models.ChatLog) -> tuple[object, int, int]:
    branch_rank = 0 if normalize_branch_id(row.branch_id) == DEFAULT_BRANCH_ID else 1
    return (row.timestamp, branch_rank, int(row.id or 0))


def create_chat_log(
    db: Session,
    agent_id: int,
    user_message: str,
    agent_reply: str,
    branch_id: str = "main",
    session_id: str = "default_session",
    experiment_mode: str = "mode_alpha",
    topic: str = "general",
) -> models.ChatLog:
    """Persist a user-agent private chat turn with second-level precision."""
    normalized_branch_id = normalize_branch_id(branch_id)
    normalized_session_id = (
        (session_id or "default_session").strip() or "default_session"
    )
    normalized_experiment_mode = (
        (experiment_mode or "mode_alpha").strip() or "mode_alpha"
    )
    normalized_topic = (topic or "general").strip()[:64] or "general"
    timestamp = utc_now_seconds()
    db_chat_log = models.ChatLog(
        agent_id=agent_id,
        branch_id=normalized_branch_id,
        session_id=normalized_session_id,
        experiment_mode=normalized_experiment_mode,
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
    filters = [
        models.ChatLog.agent_id == agent_id,
        models.ChatLog.branch_id.in_(branch_scope_ids(normalized_branch_id)),
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
    return sorted(rows, key=_chat_replay_sort_key)


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
    filters = [
        models.ChatLog.agent_id == agent_id,
        models.ChatLog.branch_id.in_(branch_scope_ids(normalized_branch_id)),
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
    return sorted(rows, key=_chat_replay_sort_key)
