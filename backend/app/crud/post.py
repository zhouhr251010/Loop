"""Database operations for public-square posts."""

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.schemas.post import PostCreate


def get_post(db: Session, post_id: int) -> models.Post | None:
    """Return a post by primary key."""
    return db.query(models.Post).filter(models.Post.id == post_id).first()


def create_post(db: Session, agent_id: int, post_in: PostCreate) -> models.Post:
    """Create a post for an agent with second-level timestamp precision."""
    db_post = models.Post(
        agent_id=agent_id,
        content=post_in.content,
        timestamp=utc_now_seconds(),
    )
    db.add(db_post)
    db.commit()
    db.refresh(db_post)
    return db_post


def get_posts(db: Session, skip: int = 0, limit: int = 100) -> list[models.Post]:
    """Return public-square posts ordered newest first."""
    return (
        db.query(models.Post)
        .join(models.Agent)
        .order_by(models.Post.timestamp.desc(), models.Post.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
