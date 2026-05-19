"""Database operations for virtual agents."""

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.schemas.agent import AgentCreate
from app.services.event_store import append_event


def get_agent(db: Session, agent_id: int) -> models.Agent | None:
    """Return an agent by primary key."""
    return db.query(models.Agent).filter(models.Agent.id == agent_id).first()


def get_agents(db: Session, include_npc: bool = True) -> list[models.Agent]:
    """Return all agents in the simulation world."""
    query = db.query(models.Agent)
    if not include_npc:
        query = query.filter(models.Agent.is_npc.is_(False))
    return query.order_by(models.Agent.is_npc.desc(), models.Agent.id.asc()).all()


def get_agent_by_user_id(db: Session, user_id: int) -> models.Agent | None:
    """Return the agent associated with a user, if one exists."""
    return db.query(models.Agent).filter(models.Agent.user_id == user_id).first()


def get_agent_by_username(db: Session, username: str) -> models.Agent | None:
    """Return the agent associated with a username, if one exists."""
    return (
        db.query(models.Agent)
        .join(models.User)
        .filter(func.lower(models.User.username) == username.lower())
        .first()
    )


def build_system_prompt_base(user: models.User) -> str:
    """Build the initial deterministic system prompt from questionnaire data."""
    profile = {
        "mbti_type": user.mbti_type,
        "big_five_scores": user.big_five_scores,
        "schwartz_values": user.schwartz_values,
        "autobiography": user.autobiography,
        "core_memory": user.core_memory,
    }
    profile_json = json.dumps(profile, ensure_ascii=False, sort_keys=True)
    return (
        "You are a virtual research agent representing this user's personality "
        f"and values profile. Use the following profile as your base context: {profile_json}"
    )


def create_agent(db: Session, agent_in: AgentCreate) -> models.Agent:
    """Create a new virtual agent."""
    timestamp = utc_now_seconds()
    db_agent = models.Agent(**agent_in.model_dump())
    db.add(db_agent)
    db.flush()
    db_user = db.get(models.User, db_agent.user_id)
    append_event(
        db,
        agent_id=db_agent.id,
        event_type="AGENT_CREATED",
        payload={
            "user_id": db_agent.user_id,
            "agent_name": db_agent.agent_name,
            "system_prompt_base": db_agent.system_prompt_base,
            "is_npc": db_agent.is_npc,
            "core_memory": db_user.core_memory if db_user is not None else None,
        },
        timestamp=timestamp,
        commit=False,
    )
    db.commit()
    db.refresh(db_agent)
    return db_agent


def create_or_update_agent_for_user(db: Session, user: models.User) -> models.Agent:
    """Create a user's agent, or update the existing one to preserve one-to-one semantics."""
    agent_name = f"{user.username}_Agent"
    system_prompt_base = build_system_prompt_base(user)
    db_agent = get_agent_by_user_id(db, user.id)

    if db_agent is None:
        return create_agent(
            db,
            AgentCreate(
                user_id=user.id,
                agent_name=agent_name,
                system_prompt_base=system_prompt_base,
            ),
        )

    db_agent.agent_name = agent_name
    db_agent.system_prompt_base = system_prompt_base
    append_event(
        db,
        agent_id=db_agent.id,
        event_type="AGENT_PROFILE_UPDATED",
        payload={
            "user_id": user.id,
            "agent_name": agent_name,
            "system_prompt_base": system_prompt_base,
            "core_memory": user.core_memory,
        },
        commit=False,
    )
    db.commit()
    db.refresh(db_agent)
    return db_agent
