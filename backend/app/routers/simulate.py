"""Simulation endpoints for automatic agent posting."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import post as post_crud
from app.database import get_db
from app.models import Agent, utc_now_seconds
from app.schemas.post import PostCreate, PostOut
from app.security import require_admin_or_machine_key
from app.services.branching import branch_exists, normalize_branch_id
from app.services.llm_service import LLMPostGenerationError, generate_agent_post
from app.services.time_machine import TimeMachine


router = APIRouter(prefix="/api/simulate", tags=["simulation"])
logger = logging.getLogger(__name__)


async def _simulate_agent_post(
    db: Session,
    agent: Agent,
    branch_id: str = "main",
) -> PostOut:
    """Generate and persist one simulated post for an agent."""
    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    reconstructed_state = TimeMachine(db).reconstruct_state(
        agent_id=agent.id,
        target_timestamp=utc_now_seconds(),
        branch_id=normalized_branch_id,
    )
    reconstructed_core_memory = str(
        reconstructed_state.get("current_core_memory") or "",
    ).strip()

    try:
        generated_content = await generate_agent_post(
            agent.user,
            branch_id=normalized_branch_id,
            reconstructed_core_memory=reconstructed_core_memory,
        )
    except LLMPostGenerationError as exc:
        logger.exception(
            "[Simulation] Agent post generation failed. "
            "agent_id=%s branch_id=%s",
            agent.id,
            normalized_branch_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM post generation failed: {exc}",
        ) from exc

    post_in = PostCreate(content=generated_content, branch_id=normalized_branch_id)
    db_post = post_crud.create_post(db, agent.id, post_in)
    return PostOut(
        id=db_post.id,
        agent_id=db_post.agent_id,
        content=db_post.content,
        timestamp=db_post.timestamp,
        branch_id=normalized_branch_id,
    )


@router.post(
    "/agent/{agent_id}/post",
    response_model=PostOut,
    status_code=status.HTTP_201_CREATED,
)
async def simulate_single_agent_post(
    agent_id: int,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin_or_machine: object = Depends(require_admin_or_machine_key),
) -> PostOut:
    """Generate a post for one agent from its owner's identity-core data."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    return await _simulate_agent_post(db, db_agent, branch_id)


@router.post(
    "/user/{username}/post",
    response_model=PostOut,
    status_code=status.HTTP_201_CREATED,
)
async def simulate_user_agent_post(
    username: str,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin_or_machine: object = Depends(require_admin_or_machine_key),
) -> PostOut:
    """Generate a post for the Agent owned by a username."""
    db_agent = agent_crud.get_agent_by_username(db, username.strip())
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found for this username.",
        )

    return await _simulate_agent_post(db, db_agent, branch_id)


@router.post(
    "/tick",
    response_model=list[PostOut],
    status_code=status.HTTP_201_CREATED,
)
async def simulate_tick(
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin_or_machine: object = Depends(require_admin_or_machine_key),
) -> list[PostOut]:
    """Advance the simulation clock by asking every agent to publish one post."""
    agents = agent_crud.get_agents(db, include_npc=False)
    posts: list[PostOut] = []
    for agent in agents:
        posts.append(await _simulate_agent_post(db, agent, branch_id))
    return posts
