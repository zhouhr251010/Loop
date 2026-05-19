"""Probe-response endpoints for M1-M6 validation data collection."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models
from app.crud import agent as agent_crud
from app.database import get_db
from app.models import utc_now_seconds
from app.schemas.probes import (
    ProbeSubmitBatchRequest,
    ProbeStatusResponse,
    ProbeSubmitResponse,
)
from app.security import get_current_user
from app.services.access_control import resolve_target_user_for_agent
from app.services.branching import DEFAULT_BRANCH_ID, branch_exists, normalize_branch_id
from app.services.core_memory_service import normalize_core_memory
from app.services.event_store import append_event
from app.services.scoring_service import (
    compact_score_summary,
    merge_questionnaire_scores_into_core_memory,
    score_probe_responses,
)
from app.services.time_machine import TimeMachine


router = APIRouter(prefix="/api/probes", tags=["probes"])
logger = logging.getLogger(__name__)


@router.get("/status", response_model=ProbeStatusResponse)
def get_probe_status(
    agent_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ProbeStatusResponse:
    """Return whether the user needs this week's IPIP-120 baseline update."""
    _, target_user = resolve_target_user_for_agent(db, current_user, agent_id)
    last_response = (
        db.query(models.ProbeResponse)
        .filter(
            models.ProbeResponse.user_id == str(target_user.id),
            models.ProbeResponse.branch_id == DEFAULT_BRANCH_ID,
            models.ProbeResponse.responder == "human",
            models.ProbeResponse.probe_set == "IPIP120",
        )
        .order_by(models.ProbeResponse.timestamp.desc())
        .first()
    )
    last_submitted = last_response.timestamp if last_response is not None else None
    needs_update = (
        last_submitted is None
        or last_submitted < utc_now_seconds() - timedelta(days=7)
    )

    return ProbeStatusResponse(
        needs_update=needs_update,
        last_submitted=last_submitted,
    )


@router.post(
    "/submit",
    response_model=ProbeSubmitResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_probe_responses(
    submission: ProbeSubmitBatchRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ProbeSubmitResponse:
    """Store authenticated human baseline probe responses in bulk."""
    target_agent, target_user = resolve_target_user_for_agent(
        db,
        current_user,
        submission.agent_id,
    )
    branch_id = normalize_branch_id(submission.branch_id)
    if not branch_exists(db, branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    db_responses = [
        models.ProbeResponse(
            user_id=str(target_user.id),
            branch_id=branch_id,
            probe_set=response.probe_set,
            probe_id=response.probe_id,
            answer=response.answer,
            responder="human",
        )
        for response in submission.responses
    ]

    db.add_all(db_responses)
    big_five_scores, schwartz_values = score_probe_responses(submission.responses)
    if big_five_scores is not None:
        if branch_id == DEFAULT_BRANCH_ID:
            target_user.big_five_scores = big_five_scores
    if schwartz_values is not None:
        if branch_id == DEFAULT_BRANCH_ID:
            target_user.schwartz_values = schwartz_values
    scored_any = big_five_scores is not None or schwartz_values is not None
    if scored_any:
        if branch_id == DEFAULT_BRANCH_ID:
            base_core_memory = target_user.core_memory
            profile_mbti = target_user.mbti_type
            profile_big_five = (
                big_five_scores
                if big_five_scores is not None
                else target_user.big_five_scores
            )
            profile_schwartz = (
                schwartz_values
                if schwartz_values is not None
                else target_user.schwartz_values
            )
        else:
            reconstructed_state = TimeMachine(db).reconstruct_state(
                agent_id=target_agent.id,
                target_timestamp=utc_now_seconds(),
                branch_id=branch_id,
            )
            base_core_memory = normalize_core_memory(
                reconstructed_state.get("core_memory"),
            )
            profile_mbti = None
            profile_big_five = big_five_scores
            profile_schwartz = schwartz_values
        branch_core_memory = merge_questionnaire_scores_into_core_memory(
            base_core_memory,
            profile_mbti,
            profile_big_five,
            profile_schwartz,
        )
        if branch_id == DEFAULT_BRANCH_ID:
            target_user.core_memory = branch_core_memory
        append_event(
            db,
            agent_id=target_agent.id,
            branch_id=branch_id,
            event_type="CORE_MEMORY_UPDATED",
            payload={
                "source": "probe_scoring",
                "user_id": target_user.id,
                "key": "persona_traits",
                "core_memory": branch_core_memory,
                "big_five_scores": big_five_scores,
                "schwartz_values": schwartz_values,
            },
            commit=False,
        )

    if branch_id == DEFAULT_BRANCH_ID and scored_any:
        agent_crud.create_or_update_agent_for_user(db, target_user)
    else:
        db.commit()
    logger.info(
        "[Probe Scoring] user_id=%s username=%s branch_id=%s submitted=%s "
        "ipip_scored=%s pvq_scored=%s big_five=%s schwartz=%s "
        "core_memory_profile_updated=%s",
        target_user.id,
        target_user.username,
        branch_id,
        len(db_responses),
        big_five_scores is not None,
        schwartz_values is not None,
        compact_score_summary(target_user.big_five_scores),
        compact_score_summary(target_user.schwartz_values),
        branch_id == DEFAULT_BRANCH_ID and scored_any,
    )
    return ProbeSubmitResponse(submitted=len(db_responses))
