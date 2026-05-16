"""Destructive cleanup service for deleting one Agent and its durable traces."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app import models
from app.database import IS_POSTGRES
from app.services.core_memory_service import DEFAULT_CORE_MEMORY


EVENT_LOG_DELETE_TRIGGER_SQL = """
CREATE TRIGGER event_logs_no_delete
BEFORE DELETE ON event_logs
FOR EACH ROW
EXECUTE FUNCTION prevent_event_logs_mutation()
"""


@dataclass(frozen=True)
class AgentDeletionSummary:
    """Counts returned after hard-deleting one Agent."""

    agent_id: int
    agent_name: str
    user_id: int
    is_npc: bool
    event_logs_deleted: int
    chat_logs_deleted: int
    vector_memories_deleted: int
    core_memory_cleared: bool
    reflection_events_deleted: int
    relationships_deleted: int
    posts_deleted: int
    feedback_logs_deleted: int
    evaluations_deleted: int
    users_deleted: int
    message: str


def _restore_event_log_delete_trigger(db: Session) -> None:
    if not IS_POSTGRES:
        return
    db.execute(text("DROP TRIGGER IF EXISTS event_logs_no_delete ON event_logs"))
    db.execute(text(EVENT_LOG_DELETE_TRIGGER_SQL))


def _delete_agent_event_logs(db: Session, agent_id: int) -> int:
    try:
        if IS_POSTGRES:
            db.execute(text("DROP TRIGGER IF EXISTS event_logs_no_delete ON event_logs"))
        deleted = (
            db.query(models.EventLog)
            .filter(models.EventLog.agent_id == agent_id)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
    finally:
        _restore_event_log_delete_trigger(db)


def _delete_agent_vector_memories(db: Session, agent_id: int) -> int:
    if not IS_POSTGRES:
        return 0
    participant_agent_ids = json.dumps([agent_id], ensure_ascii=False)
    result = db.execute(
        text(
            """
            DELETE FROM rag_documents
            WHERE (
              metadata ? 'agent_id'
              AND metadata ->> 'agent_id' ~ '^\\d+$'
              AND (metadata ->> 'agent_id')::integer = :agent_id
            )
            OR (
              metadata ? 'target_agent_id'
              AND metadata ->> 'target_agent_id' ~ '^\\d+$'
              AND (metadata ->> 'target_agent_id')::integer = :agent_id
            )
            OR (
              metadata ? 'sender_agent_id'
              AND metadata ->> 'sender_agent_id' ~ '^\\d+$'
              AND (metadata ->> 'sender_agent_id')::integer = :agent_id
            )
            OR (
              metadata ? 'original_speaker_id'
              AND metadata ->> 'original_speaker_id' ~ '^\\d+$'
              AND (metadata ->> 'original_speaker_id')::integer = :agent_id
            )
            OR (
              metadata ? 'participant_agent_ids'
              AND metadata -> 'participant_agent_ids' @> CAST(:participant_agent_ids AS jsonb)
            )
            """,
        ),
        {
            "agent_id": agent_id,
            "participant_agent_ids": participant_agent_ids,
        },
    )
    return int(result.rowcount or 0)


def delete_agent_and_traces(db: Session, agent: models.Agent) -> AgentDeletionSummary:
    """Hard-delete one Agent after removing dependent rows and vector memories."""
    agent_id = int(agent.id)
    user_id = int(agent.user_id)
    agent_name = str(agent.agent_name)
    is_npc = bool(agent.is_npc)
    user = agent.user

    post_ids = [
        row[0]
        for row in db.query(models.Post.id)
        .filter(models.Post.agent_id == agent_id)
        .all()
    ]

    feedback_filters = []
    if post_ids:
        feedback_filters.append(models.FeedbackLog.post_id.in_(post_ids))
    if is_npc:
        feedback_filters.append(models.FeedbackLog.user_id == user_id)

    feedback_logs_deleted = 0
    if feedback_filters:
        feedback_logs_deleted = (
            db.query(models.FeedbackLog)
            .filter(or_(*feedback_filters))
            .delete(synchronize_session=False)
        )

    relationships_deleted = (
        db.query(models.Relationship)
        .filter(
            or_(
                models.Relationship.agent_id_1 == agent_id,
                models.Relationship.agent_id_2 == agent_id,
            ),
        )
        .delete(synchronize_session=False)
    )
    evaluations_deleted = (
        db.query(models.Evaluation)
        .filter(models.Evaluation.agent_id == agent_id)
        .delete(synchronize_session=False)
    )
    reflection_events_deleted = (
        db.query(models.ReflectionEvent)
        .filter(models.ReflectionEvent.agent_id == agent_id)
        .delete(synchronize_session=False)
    )
    chat_logs_deleted = (
        db.query(models.ChatLog)
        .filter(models.ChatLog.agent_id == agent_id)
        .delete(synchronize_session=False)
    )
    posts_deleted = (
        db.query(models.Post)
        .filter(models.Post.agent_id == agent_id)
        .delete(synchronize_session=False)
    )
    vector_memories_deleted = _delete_agent_vector_memories(db, agent_id)
    core_memory_cleared = False
    if user is not None:
        user.core_memory = DEFAULT_CORE_MEMORY.copy()
        if is_npc:
            user.autobiography = None
            user.mbti_type = None
            user.big_five_scores = None
            user.schwartz_values = None
        core_memory_cleared = True

    event_logs_deleted = _delete_agent_event_logs(db, agent_id)

    db.delete(agent)
    users_deleted = 0
    if is_npc and user is not None:
        db.delete(user)
        users_deleted = 1

    db.commit()
    return AgentDeletionSummary(
        agent_id=agent_id,
        agent_name=agent_name,
        user_id=user_id,
        is_npc=is_npc,
        event_logs_deleted=event_logs_deleted,
        chat_logs_deleted=int(chat_logs_deleted or 0),
        vector_memories_deleted=vector_memories_deleted,
        core_memory_cleared=core_memory_cleared,
        reflection_events_deleted=int(reflection_events_deleted or 0),
        relationships_deleted=int(relationships_deleted or 0),
        posts_deleted=int(posts_deleted or 0),
        feedback_logs_deleted=int(feedback_logs_deleted or 0),
        evaluations_deleted=int(evaluations_deleted or 0),
        users_deleted=users_deleted,
        message=f"Deleted Agent #{agent_id} and its associated durable traces.",
    )
