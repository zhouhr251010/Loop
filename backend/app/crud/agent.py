"""Database operations for virtual agents."""

import json

from sqlalchemy.orm import Session

from app import models
from app.schemas.agent import AgentCreate


def get_agent(db: Session, agent_id: int) -> models.Agent | None:
    """Return an agent by primary key."""
    return db.query(models.Agent).filter(models.Agent.id == agent_id).first()


def get_agents(db: Session) -> list[models.Agent]:
    """Return all agents in the simulation world."""
    return db.query(models.Agent).order_by(models.Agent.id.asc()).all()


def get_agent_by_user_id(db: Session, user_id: int) -> models.Agent | None:
    """Return the agent associated with a user, if one exists."""
    return db.query(models.Agent).filter(models.Agent.user_id == user_id).first()


def build_system_prompt_base(user: models.User) -> str:
    """Build the initial deterministic system prompt from questionnaire data."""
    profile = {
        "mbti_type": user.mbti_type,
        "big_five_scores": user.big_five_scores,
        "schwartz_values": user.schwartz_values,
        "autobiography": user.autobiography,
    }
    profile_json = json.dumps(profile, ensure_ascii=False, sort_keys=True)
    return (
        "You are a virtual research agent representing this user's personality "
        f"and values profile. Use the following profile as your base context: {profile_json}"
    )


def create_agent(db: Session, agent_in: AgentCreate) -> models.Agent:
    """Create a new virtual agent."""
    db_agent = models.Agent(**agent_in.model_dump())
    db.add(db_agent)
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
    db.commit()
    db.refresh(db_agent)
    return db_agent
