"""Counterfactual anchor endpoints for identity-memory collection."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.models import utc_now_seconds
from app.schemas.counterfactuals import (
    CounterfactualSubmitRequest,
    CounterfactualSubmitResponse,
)
from app.security import get_current_user
from app.services.core_memory_service import normalize_core_memory
from app.services.event_store import append_event


router = APIRouter(prefix="/api/counterfactuals", tags=["counterfactuals"])


def _format_anchor_memory(anchor: CounterfactualSubmitRequest) -> str:
    return (
        "[反事实锚点] "
        f"背景：{anchor.decision_context.strip()} "
        f"如果：{anchor.counterfactual_action.strip()} "
        f"结果：{anchor.counterfactual_result.strip()}"
    )


@router.post(
    "/submit",
    response_model=CounterfactualSubmitResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_counterfactual_anchor(
    anchor: CounterfactualSubmitRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> CounterfactualSubmitResponse:
    """Store a counterfactual anchor and append it to persona core memory."""
    if current_user.agent is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please complete questionnaire onboarding before adding anchors.",
        )

    timestamp = utc_now_seconds()
    anchor_memory = _format_anchor_memory(anchor)
    payload = {
        "user_id": current_user.id,
        "decision_context": anchor.decision_context,
        "counterfactual_action": anchor.counterfactual_action,
        "counterfactual_result": anchor.counterfactual_result,
        "description": anchor_memory,
    }

    append_event(
        db,
        agent_id=current_user.agent.id,
        event_type="COUNTERFACTUAL_ANCHOR_CREATED",
        payload=payload,
        timestamp=timestamp,
        commit=False,
    )

    core_memory = normalize_core_memory(current_user.core_memory)
    existing_traits = core_memory["persona_traits"].strip()
    anchor_line = f"- {anchor_memory}"
    if anchor_memory not in existing_traits:
        core_memory["persona_traits"] = (
            f"{existing_traits}\n{anchor_line}".strip()
        )[-8000:]

    current_user.core_memory = core_memory
    append_event(
        db,
        agent_id=current_user.agent.id,
        event_type="CORE_MEMORY_UPDATED",
        payload={
            "source": "counterfactual_anchor",
            "user_id": current_user.id,
            "key": "persona_traits",
            "appended_anchor": anchor_memory,
            "core_memory": core_memory,
        },
        timestamp=timestamp,
        commit=False,
    )

    db.commit()
    db.refresh(current_user)
    return CounterfactualSubmitResponse(saved=True, core_memory_updated=True)
