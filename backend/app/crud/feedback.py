"""Database operations for user correction feedback logs."""

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.schemas.feedback import FeedbackCreate
from app.services.branching import normalize_branch_id
from app.services.event_store import append_event


def create_feedback_log(
    db: Session,
    post: models.Post,
    feedback_in: FeedbackCreate,
) -> models.FeedbackLog:
    """Create a feedback record using the post content as original text."""
    timestamp = utc_now_seconds()
    branch_id = normalize_branch_id(feedback_in.branch_id)
    db_feedback = models.FeedbackLog(
        post_id=post.id,
        user_id=feedback_in.user_id,
        original_text=post.content,
        corrected_text=feedback_in.corrected_text,
        timestamp=timestamp,
        context_embedding_id=None,
    )
    db.add(db_feedback)
    db.flush()
    append_event(
        db,
        agent_id=post.agent_id,
        branch_id=branch_id,
        event_type="FEEDBACK_CREATED",
        payload={
            "feedback_log_id": db_feedback.id,
            "post_id": post.id,
            "branch_id": branch_id,
            "user_id": feedback_in.user_id,
            "original_text": post.content,
            "corrected_text": feedback_in.corrected_text,
        },
        timestamp=timestamp,
        commit=False,
    )
    db.commit()
    db.refresh(db_feedback)
    return db_feedback
