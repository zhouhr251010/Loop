"""Database operations for private agent chat logs."""

from time import sleep

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds


def _is_sqlite_busy(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def create_chat_log(
    db: Session,
    agent_id: int,
    user_message: str,
    agent_reply: str,
) -> models.ChatLog:
    """Persist a user-agent private chat turn with second-level precision."""
    for attempt in range(3):
        db_chat_log = models.ChatLog(
            agent_id=agent_id,
            user_message=user_message,
            agent_reply=agent_reply,
            timestamp=utc_now_seconds(),
        )
        db.add(db_chat_log)
        try:
            db.commit()
            db.refresh(db_chat_log)
            return db_chat_log
        except OperationalError as exc:
            db.rollback()
            if attempt == 2 or not _is_sqlite_busy(exc):
                raise
            sleep(0.15 * (attempt + 1))

    raise RuntimeError("Failed to persist chat log.")
