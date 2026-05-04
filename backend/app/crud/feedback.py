"""Database operations for user correction feedback logs."""

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.schemas.feedback import FeedbackCreate


def create_feedback_log(
    db: Session,
    post: models.Post,
    feedback_in: FeedbackCreate,
) -> models.FeedbackLog:
    """Create a feedback record using the post content as original text."""
    db_feedback = models.FeedbackLog(
        post_id=post.id,
        user_id=feedback_in.user_id,
        original_text=post.content,
        corrected_text=feedback_in.corrected_text,
        timestamp=utc_now_seconds(),
        context_embedding_id=None,
    )
    db.add(db_feedback)
    db.commit()
    db.refresh(db_feedback)
    return db_feedback
