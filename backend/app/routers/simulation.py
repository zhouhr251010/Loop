"""Event-sourced simulation timeline endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.schemas.system_settings import SystemSettingsOut, SystemSettingsPatch
from app.security import get_current_user, require_admin
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    branch_exists,
    branch_window_filter,
    coerce_timestamp,
    get_branch_read_windows,
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


def _get_or_create_system_settings(db: Session) -> models.SystemSetting:
    settings = db.get(models.SystemSetting, 1)
    if settings is not None:
        return settings

    settings = models.SystemSetting(
        id=1,
        allow_user_branch_switch=False,
        global_active_branch=DEFAULT_BRANCH_ID,
    )
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


@router.get(
    "/api/simulation/settings",
    response_model=SystemSettingsOut,
)
def get_simulation_settings(
    db: Session = Depends(get_db),
) -> models.SystemSetting:
    """Return public global experiment exposure settings."""
    return _get_or_create_system_settings(db)


@router.patch(
    "/api/simulation/settings",
    response_model=SystemSettingsOut,
)
def update_simulation_settings(
    settings_in: SystemSettingsPatch,
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
) -> models.SystemSetting:
    """Update global experiment exposure settings."""
    settings = _get_or_create_system_settings(db)

    if settings_in.global_active_branch is not None:
        branch_id = normalize_branch_id(settings_in.global_active_branch)
        if not branch_exists(db, branch_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Global active branch not found.",
            )
        settings.global_active_branch = branch_id

    if settings_in.allow_user_branch_switch is not None:
        settings.allow_user_branch_switch = settings_in.allow_user_branch_switch

    settings.updated_at = models.utc_now_seconds()
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


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

    read_windows = get_branch_read_windows(
        db,
        source_branch_id,
        requested_timestamp or source_event_timestamp,
    )
    visible_event = (
        db.query(models.EventLog.event_id)
        .filter(
            models.EventLog.event_id == source_event.event_id,
            branch_window_filter(
                models.EventLog.branch_id,
                models.EventLog.timestamp,
                models.EventLog.event_id,
                read_windows,
            ),
        )
        .first()
    )
    if visible_event is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source event is not visible from the selected source branch at that timestamp.",
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
    current_user: models.User = Depends(get_current_user),
) -> list[models.EventLog]:
    """Return a bounded slice of one agent's immutable event timeline."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if not current_user.is_admin and db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only inspect your own agent events.",
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
    current_user: models.User = Depends(get_current_user),
) -> list[str]:
    """Return all global world-line branch ids after agent access is verified."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    is_owner = db_agent.user_id == current_user.id
    if not current_user.is_admin and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You can only list branches for your own agent."
            ),
        )

    return get_global_branch_ids(db)


@router.get(
    "/api/simulation/branches",
    response_model=list[str],
)
def get_global_branches(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[str]:
    """Return all global world-line branch ids visible to signed-in users."""
    return get_global_branch_ids(db)


@router.post(
    "/api/simulation/fork",
    response_model=SimulationForkOut,
    status_code=status.HTTP_201_CREATED,
)
def fork_simulation_timeline(
    fork_in: SimulationForkCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> SimulationForkOut:
    """Fork an agent timeline and inject one counterfactual event."""
    db_agent = agent_crud.get_agent(db, fork_in.agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    is_owner = db_agent.user_id == current_user.id
    if not current_user.is_admin and not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You can only fork your own agent timeline."
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
            "parent_event_branch_id": normalize_branch_id(source_event.branch_id)
            if source_event
            else source_branch_id,
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
