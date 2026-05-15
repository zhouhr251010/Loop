"""Event-sourced simulation timeline endpoints."""

import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models
from app.crud import agent as agent_crud
from app.database import get_db
from app.schemas.event import (
    AgentStateOut,
    EventLogOut,
    SimulationForkCreate,
    SimulationForkOut,
)
from app.security import get_current_user
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    branch_exists,
    coerce_timestamp,
    get_branch_lineage_ids,
    get_global_branch_ids,
    normalize_branch_id,
)
from app.services.event_store import append_event
from app.services.time_machine import TimeMachine

router = APIRouter(tags=["simulation"])
logger = logging.getLogger(__name__)


def _counterfactual_event_type(counterfactual_event: dict[str, Any]) -> str:
    event_type = counterfactual_event.get("event_type")
    if isinstance(event_type, str) and event_type.strip():
        return event_type.strip().upper()
    return "COUNTERFACTUAL_EVENT"


def _is_valid_admin_key(x_loop_admin_key: str | None) -> bool:
    configured_key = os.getenv("LOOP_ADMIN_API_KEY")
    return bool(
        configured_key
        and x_loop_admin_key
        and hmac.compare_digest(x_loop_admin_key, configured_key)
    )


def _get_optional_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.User | None:
    if not authorization:
        return None
    try:
        return get_current_user(authorization=authorization, db=db)
    except HTTPException:
        return None


def _validate_source_event(
    db: Session,
    *,
    agent_id: int,
    source_branch_id: str,
    source_event_id: int | None,
    rollback_timestamp: Any,
) -> models.EventLog | None:
    if source_event_id is None:
        return None

    source_event = db.get(models.EventLog, source_event_id)
    if source_event is None or source_event.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source event not found for this agent.",
        )

    source_lineage = get_branch_lineage_ids(db, source_branch_id)
    event_branch_id = normalize_branch_id(source_event.branch_id)
    if event_branch_id not in source_lineage:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source event is not part of the selected source branch lineage.",
        )

    source_event_timestamp = coerce_timestamp(source_event.timestamp)
    requested_timestamp = coerce_timestamp(rollback_timestamp)
    if (
        source_event_timestamp is not None
        and requested_timestamp is not None
        and source_event_timestamp != requested_timestamp
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source event timestamp does not match rollback_timestamp.",
        )

    return source_event


@router.get(
    "/api/agents/{agent_id}/events",
    response_model=list[EventLogOut],
)
def get_agent_events(
    agent_id: int,
    branch_id: str = "main",
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[models.EventLog]:
    """Return a bounded slice of one agent's immutable event timeline."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    normalized_branch_id = normalize_branch_id(branch_id)
    events = (
        db.query(models.EventLog)
        .filter(
            models.EventLog.agent_id == agent_id,
            models.EventLog.branch_id == normalized_branch_id,
        )
        .order_by(models.EventLog.timestamp.desc(), models.EventLog.event_id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return list(reversed(events))


@router.get(
    "/api/simulation/agents/{agent_id}/branches",
    response_model=list[str],
)
def get_agent_branches(
    agent_id: int,
    db: Session = Depends(get_db),
    x_loop_admin_key: str | None = Header(default=None),
    current_user: models.User | None = Depends(_get_optional_current_user),
) -> list[str]:
    """Return all global world-line branch ids after agent access is verified."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    is_admin = _is_valid_admin_key(x_loop_admin_key)
    is_owner = current_user is not None and db_agent.user_id == current_user.id
    if not is_admin and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You can only list branches for your own agent, or provide a "
                "valid X-Loop-Admin-Key."
            ),
        )

    return get_global_branch_ids(db)


@router.get(
    "/api/simulation/branches",
    response_model=list[str],
)
def get_global_branches(
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(_get_optional_current_user),
) -> list[str]:
    """Return all global world-line branch ids visible to signed-in users."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return get_global_branch_ids(db)


@router.post(
    "/api/simulation/fork",
    response_model=SimulationForkOut,
    status_code=status.HTTP_201_CREATED,
)
def fork_simulation_timeline(
    fork_in: SimulationForkCreate,
    db: Session = Depends(get_db),
    x_loop_admin_key: str | None = Header(default=None),
    current_user: models.User | None = Depends(_get_optional_current_user),
) -> SimulationForkOut:
    """Fork an agent timeline and inject one counterfactual event."""
    db_agent = agent_crud.get_agent(db, fork_in.agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    is_admin = _is_valid_admin_key(x_loop_admin_key)
    is_owner = current_user is not None and db_agent.user_id == current_user.id
    if not is_admin and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You can only fork your own agent timeline, or provide a valid "
                "X-Loop-Admin-Key."
            ),
        )

    new_branch_name = normalize_branch_id(fork_in.new_branch_name)
    if new_branch_name == DEFAULT_BRANCH_ID:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot fork over the main branch.",
        )
    if branch_exists(db, new_branch_name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Global branch already exists.",
        )

    source_branch_id = normalize_branch_id(fork_in.source_branch_id)
    if not branch_exists(db, source_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source branch not found.",
        )

    source_event = _validate_source_event(
        db,
        agent_id=fork_in.agent_id,
        source_branch_id=source_branch_id,
        source_event_id=fork_in.source_event_id,
        rollback_timestamp=fork_in.rollback_timestamp,
    )

    time_machine = TimeMachine(db)
    reconstructed_state = time_machine.reconstruct_state(
        agent_id=fork_in.agent_id,
        target_timestamp=fork_in.rollback_timestamp,
        branch_id=source_branch_id,
    )
    event_payload = {
        "fork": {
            "scope": "global_world_line",
            "from_branch_id": source_branch_id,
            "parent_branch_id": source_branch_id,
            "parent_event_id": source_event.event_id if source_event else None,
            "rollback_timestamp": fork_in.rollback_timestamp,
            "base_state": reconstructed_state,
            "seed_agent_id": fork_in.agent_id,
        },
        "counterfactual_event": fork_in.counterfactual_event,
    }
    injected_event = append_event(
        db,
        agent_id=fork_in.agent_id,
        branch_id=new_branch_name,
        event_type=_counterfactual_event_type(fork_in.counterfactual_event),
        payload=event_payload,
        timestamp=fork_in.rollback_timestamp,
    )
    logger.info(
        f"[Time Machine] Forked new timeline '{new_branch_name}' from "
        f"{source_branch_id} at {fork_in.rollback_timestamp}. "
        "Injected counterfactual event.",
    )

    return SimulationForkOut(
        branch_id=new_branch_name,
        rollback_timestamp=fork_in.rollback_timestamp,
        injected_event=injected_event,
        reconstructed_state=AgentStateOut.model_validate(reconstructed_state),
    )
