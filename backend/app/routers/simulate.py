"""Simulation endpoints for automatic agent posting."""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import post as post_crud
from app.database import get_db
from app.models import Agent, utc_now_seconds
from app.schemas.debate import DebateTriggerRequest, DebateTriggerResponse
from app.schemas.post import PostCreate, PostOut
from app.security import require_admin_or_machine_key
from app.services.branching import branch_exists, normalize_branch_id
from app.services.debate_graph import run_debate
from app.services.llm_service import LLMPostGenerationError, generate_agent_post
from app.services.speaker_manager import (
    is_timeout_exception as is_speaker_timeout_exception,
    trigger_agent_group_turn,
)
from app.services.time_machine import TimeMachine


router = APIRouter(prefix="/api/simulate", tags=["simulation"])
logger = logging.getLogger(__name__)


def _parse_agent_ids(raw_agent_ids: list[str]) -> list[int]:
    """Convert validated request Agent ids into integers."""
    return [int(agent_id) for agent_id in raw_agent_ids]


def _parse_final_report(value: object) -> dict[str, Any] | str:
    """Return the report as parsed JSON when possible."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return str(value or "")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return parsed if isinstance(parsed, dict) else value


def _is_timeout_exception(exc: Exception) -> bool:
    """Classify common OpenAI/httpx/asyncio timeout failures without tight coupling."""
    class_names = {
        exc.__class__.__name__.lower(),
        *(base.__name__.lower() for base in exc.__class__.__mro__),
    }
    message = str(exc).lower()
    return any("timeout" in name for name in class_names) or "timed out" in message


def _build_initial_debate_messages(
    topic: str,
    participant_agent_ids: list[int],
) -> list[dict[str, Any]]:
    """Create a small moderator kickoff message for the debate state."""
    return [
        {
            "role": "moderator",
            "speaker_id": None,
            "speaker_name": "Loop Debate Moderator",
            "content": (
                "监督者已启动本轮多智能体辩论。"
                f"主题：{topic}。"
                f"参与 Agent：{participant_agent_ids}。"
                "请各 Agent 只代表自己的视角发言。"
            ),
        },
    ]


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
            reconstructed_core_memory=reconstructed_core_memory
            if normalized_branch_id != "main"
            else None,
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
    "/debate",
    response_model=DebateTriggerResponse,
    status_code=status.HTTP_200_OK,
)
async def simulate_debate(
    debate_in: DebateTriggerRequest,
    db: Session = Depends(get_db),
    _admin_or_machine: object = Depends(require_admin_or_machine_key),
) -> DebateTriggerResponse:
    """Run a branch-bound supervised debate among selected Agents."""
    normalized_branch_id = normalize_branch_id(debate_in.branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    participant_agent_ids = _parse_agent_ids(debate_in.participant_agent_ids)
    existing_agent_ids = {
        int(row[0])
        for row in (
            db.query(Agent.id)
            .filter(Agent.id.in_(participant_agent_ids))
            .all()
        )
    }
    missing_agent_ids = [
        agent_id
        for agent_id in participant_agent_ids
        if agent_id not in existing_agent_ids
    ]
    if missing_agent_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": "One or more participant Agents were not found.",
                "missing_agent_ids": missing_agent_ids,
            },
        )

    try:
        result = await run_debate(
            db,
            topic=debate_in.topic,
            participants=participant_agent_ids,
            branch_id=normalized_branch_id,
            session_id=f"debate:{normalized_branch_id}:{utc_now_seconds().isoformat()}",
            max_turns=debate_in.max_turns,
            initial_messages=_build_initial_debate_messages(
                debate_in.topic,
                participant_agent_ids,
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(
            "[Simulation] Debate run failed. branch_id=%s participant_agent_ids=%s",
            normalized_branch_id,
            participant_agent_ids,
        )
        if _is_timeout_exception(exc):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Debate LLM request timed out or service was unavailable.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Debate graph execution failed.",
        ) from exc

    return DebateTriggerResponse(
        status="completed",
        turns_executed=int(result.get("turns_count") or 0),
        consensus_reached=bool(result.get("is_consensus_reached")),
        final_report=_parse_final_report(result.get("final_report")),
    )


@router.post(
    "/groups/{group_id}/tick",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def simulate_agent_group_tick(
    group_id: str,
    branch_id: str = "main",
    db: Session = Depends(get_db),
    _admin_or_machine: object = Depends(require_admin_or_machine_key),
) -> dict[str, Any]:
    """Manually advance one Agent-only group by exactly one speaker turn."""
    try:
        return await trigger_agent_group_turn(
            group_id=group_id,
            branch_id=branch_id,
            db=db,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in message.lower()
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        raise HTTPException(status_code=status_code, detail=message) from exc
    except Exception as exc:
        logger.exception(
            "[Simulation] Agent group tick failed. group_id=%s branch_id=%s",
            group_id,
            branch_id,
        )
        if is_speaker_timeout_exception(exc):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent group turn LLM request timed out or service was unavailable.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent group turn failed.",
        ) from exc


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
