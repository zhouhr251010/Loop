"""Probe-response endpoints for M1-M6 validation data collection."""

import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from app import models
from app.crud import agent as agent_crud
from app.database import get_db
from app.models import utc_now_seconds
from app.schemas.probes import (
    ProbeStatusResponse,
    ProbeSubmitRequest,
    ProbeSubmitResponse,
)
from app.security import get_current_user
from app.services.scoring_service import (
    compact_score_summary,
    merge_questionnaire_scores_into_core_memory,
    score_probe_responses,
)


router = APIRouter(prefix="/api/probes", tags=["probes"])
logger = logging.getLogger(__name__)


@router.get("/status", response_model=ProbeStatusResponse)
def get_probe_status(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ProbeStatusResponse:
    """Return whether the user needs this week's IPIP-120 baseline update."""
    last_response = (
        db.query(models.ProbeResponse)
        .filter(
            models.ProbeResponse.user_id == str(current_user.id),
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
    responses: Annotated[
        list[ProbeSubmitRequest],
        Body(..., min_length=1, max_length=300),
    ],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ProbeSubmitResponse:
    """Store authenticated human baseline probe responses in bulk."""
    db_responses = [
        models.ProbeResponse(
            user_id=str(current_user.id),
            probe_set=response.probe_set,
            probe_id=response.probe_id,
            answer=response.answer,
            responder="human",
        )
        for response in responses
    ]

    db.add_all(db_responses)
    big_five_scores, schwartz_values = score_probe_responses(responses)
    if big_five_scores is not None:
        current_user.big_five_scores = big_five_scores
    if schwartz_values is not None:
        current_user.schwartz_values = schwartz_values
    if big_five_scores is not None or schwartz_values is not None:
        current_user.core_memory = merge_questionnaire_scores_into_core_memory(
            current_user.core_memory,
            current_user.mbti_type,
            current_user.big_five_scores,
            current_user.schwartz_values,
        )

    if current_user.agent is not None and (
        big_five_scores is not None or schwartz_values is not None
    ):
        agent_crud.create_or_update_agent_for_user(db, current_user)
    else:
        db.commit()
    logger.info(
        "[Probe Scoring] user_id=%s username=%s submitted=%s "
        "ipip_scored=%s pvq_scored=%s big_five=%s schwartz=%s "
        "core_memory_profile_updated=%s",
        current_user.id,
        current_user.username,
        len(db_responses),
        big_five_scores is not None,
        schwartz_values is not None,
        compact_score_summary(current_user.big_five_scores),
        compact_score_summary(current_user.schwartz_values),
        big_five_scores is not None or schwartz_values is not None,
    )
    return ProbeSubmitResponse(submitted=len(db_responses))
