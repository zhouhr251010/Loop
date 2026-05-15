"""Counterfactual anchor endpoints for identity-memory collection."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.models import utc_now_seconds
from app.schemas.counterfactuals import (
    CounterfactualSuggestion,
    CounterfactualSubmitRequest,
    CounterfactualSubmitResponse,
)
from app.security import get_current_user
from app.services.core_memory_service import normalize_core_memory
from app.services.event_store import append_event
from app.services.llm_service import suggest_counterfactual_anchors


router = APIRouter(prefix="/api/counterfactuals", tags=["counterfactuals"])
SUGGESTION_CHAT_LIMIT = 60
SUGGESTION_POST_LIMIT = 30


def _format_anchor_memory(anchor: CounterfactualSubmitRequest) -> str:
    actual_choice = str(anchor.actual_choice or "").strip()
    actual_result = str(anchor.actual_result or "").strip()
    actual_choice_part = f"现实选择：{actual_choice} " if actual_choice else ""
    actual_result_part = f"现实结果：{actual_result} " if actual_result else ""
    return (
        "[反事实锚点] "
        f"背景：{anchor.decision_context.strip()} "
        f"{actual_choice_part}"
        f"{actual_result_part}"
        f"如果：{anchor.counterfactual_action.strip()} "
        f"结果：{anchor.counterfactual_result.strip()}"
    )


def _build_suggestion_context(
    db: Session,
    current_user: models.User,
    days: int,
) -> str:
    """Collect bounded user-owned text for suggestion generation."""
    since = utc_now_seconds() - timedelta(days=days)
    sections: list[str] = []

    autobiography = str(current_user.autobiography or "").strip()
    if autobiography:
        sections.append(f"【数字自传】\n{autobiography[:5000]}")

    agent = current_user.agent
    if agent is None:
        return "\n\n".join(sections)

    chat_rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.agent_id == agent.id,
            models.ChatLog.timestamp >= since,
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .limit(SUGGESTION_CHAT_LIMIT)
        .all()
    )
    if chat_rows:
        chat_lines = []
        for row in reversed(chat_rows):
            timestamp = row.timestamp.isoformat(sep=" ") if row.timestamp else ""
            chat_lines.append(
                (
                    f"[{timestamp}] 用户：{row.user_message}\n"
                    f"[{timestamp}] Agent：{row.agent_reply}"
                )[:1600],
            )
        sections.append("【近期私聊】\n" + "\n".join(chat_lines))

    post_rows = (
        db.query(models.Post)
        .filter(
            models.Post.agent_id == agent.id,
            models.Post.timestamp >= since,
        )
        .order_by(models.Post.timestamp.desc(), models.Post.id.desc())
        .limit(SUGGESTION_POST_LIMIT)
        .all()
    )
    if post_rows:
        post_lines = []
        for row in reversed(post_rows):
            timestamp = row.timestamp.isoformat(sep=" ") if row.timestamp else ""
            post_lines.append(f"[{timestamp}] {row.content[:800]}")
        sections.append("【近期广场帖子】\n" + "\n".join(post_lines))

    return "\n\n".join(sections)


@router.get(
    "/suggestions",
    response_model=list[CounterfactualSuggestion],
)
def suggest_counterfactual_decision_points(
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[CounterfactualSuggestion]:
    """Suggest key life decision points from autobiography and recent records."""
    context = _build_suggestion_context(db, current_user, days)
    if not context.strip():
        return []

    return [
        CounterfactualSuggestion(**suggestion)
        for suggestion in suggest_counterfactual_anchors(context)
    ]


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
        "actual_choice": anchor.actual_choice,
        "actual_result": anchor.actual_result,
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
