"""Database operations for public-square posts."""

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.schemas.post import PostCreate
from app.services.branching import normalize_branch_id
from app.services.event_store import append_event


def get_post(db: Session, post_id: int) -> models.Post | None:
    """Return a post by primary key."""
    return db.query(models.Post).filter(models.Post.id == post_id).first()


def create_post(db: Session, agent_id: int, post_in: PostCreate) -> models.Post:
    """Create a post for an agent with second-level timestamp precision."""
    timestamp = utc_now_seconds()
    branch_id = normalize_branch_id(post_in.branch_id)
    db_post = models.Post(
        agent_id=agent_id,
        content=post_in.content,
        timestamp=timestamp,
    )
    db.add(db_post)
    db.flush()
    append_event(
        db,
        agent_id=agent_id,
        branch_id=branch_id,
        event_type="POST_CREATED",
        payload={
            "post_id": db_post.id,
            "content": post_in.content,
            "branch_id": branch_id,
        },
        timestamp=timestamp,
        commit=False,
    )
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


def get_posts_for_viewer(
    db: Session,
    viewer_agent_id: int,
    skip: int = 0,
    limit: int = 100,
) -> list[models.Post]:
    """Return plaza posts biased by the viewer agent's social affinities."""
    relationship_match = and_(
        models.Relationship.agent_id_1 == viewer_agent_id,
        models.Relationship.agent_id_2 == models.Post.agent_id,
    )
    affinity = func.coalesce(models.Relationship.affinity_score, 0.0)
    return (
        db.query(models.Post)
        .join(models.Agent)
        .outerjoin(models.Relationship, relationship_match)
        .order_by(affinity.desc(), models.Post.timestamp.desc(), models.Post.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
