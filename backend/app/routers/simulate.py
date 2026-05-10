"""Simulation endpoints for automatic agent posting."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import post as post_crud
from app.database import get_db
from app.models import Agent
from app.schemas.post import PostCreate, PostOut
from app.security import require_admin_key
from app.services.llm_service import generate_agent_post


router = APIRouter(prefix="/api/simulate", tags=["simulation"])


def _simulate_agent_post(db: Session, agent: Agent) -> PostOut:
    """Generate and persist one simulated post for an agent."""
    generated_content = generate_agent_post(agent.user)
    post_in = PostCreate(content=generated_content)
    return post_crud.create_post(db, agent.id, post_in)


@router.post(
    "/agent/{agent_id}/post",
    response_model=PostOut,
    status_code=status.HTTP_201_CREATED,
)
def simulate_single_agent_post(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> PostOut:
    """Generate a post for one agent from its owner's identity-core data."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    return _simulate_agent_post(db, db_agent)


@router.post(
    "/user/{username}/post",
    response_model=PostOut,
    status_code=status.HTTP_201_CREATED,
)
def simulate_user_agent_post(
    username: str,
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> PostOut:
    """Generate a post for the Agent owned by a username."""
    db_agent = agent_crud.get_agent_by_username(db, username.strip())
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found for this username.",
        )

    return _simulate_agent_post(db, db_agent)


@router.post(
    "/tick",
    response_model=list[PostOut],
    status_code=status.HTTP_201_CREATED,
)
def simulate_tick(
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> list[PostOut]:
    """Advance the simulation clock by asking every agent to publish one post."""
    agents = agent_crud.get_agents(db)
    return [_simulate_agent_post(db, agent) for agent in agents]
