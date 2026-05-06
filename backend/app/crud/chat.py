"""Database operations for private agent chat logs."""

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds


def create_chat_log(
    db: Session,
    agent_id: int,
    user_message: str,
    agent_reply: str,
) -> models.ChatLog:
    """Persist a user-agent private chat turn with second-level precision."""
    db_chat_log = models.ChatLog(
        agent_id=agent_id,
        user_message=user_message,
        agent_reply=agent_reply,
        timestamp=utc_now_seconds(),
    )
    db.add(db_chat_log)
    db.commit()
    db.refresh(db_chat_log)
    return db_chat_log
