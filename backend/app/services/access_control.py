"""Shared RBAC helpers for agent-scoped resources."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app import models


def resolve_target_agent(
    db: Session,
    current_user: models.User,
    agent_id: int | None = None,
) -> models.Agent:
    """Resolve an agent target and enforce user/admin ownership rules."""
    if agent_id is None:
        db_agent = (
            db.query(models.Agent)
            .filter(models.Agent.user_id == current_user.id)
            .first()
        )
        if db_agent is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please complete questionnaire onboarding before this operation.",
            )
        return db_agent

    db_agent = db.get(models.Agent, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if not bool(getattr(current_user, "is_admin", False)) and (
        db_agent.user_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own Agent.",
        )
    return db_agent


def resolve_target_user_for_agent(
    db: Session,
    current_user: models.User,
    agent_id: int | None = None,
) -> tuple[models.Agent, models.User]:
    """Resolve a permitted target agent and its owning user."""
    db_agent = resolve_target_agent(db, current_user, agent_id)
    db_user = db.get(models.User, db_agent.user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent owner not found.",
        )
    return db_agent, db_user
