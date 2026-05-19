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
from app.services.access_control import resolve_target_user_for_agent
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    branch_exists,
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.core_memory_service import normalize_core_memory
from app.services.event_store import append_event
from app.services.llm_service import suggest_counterfactual_anchors
from app.services.time_machine import TimeMachine


router = APIRouter(prefix="/api/counterfactuals", tags=["counterfactuals"])
SUGGESTION_CHAT_LIMIT = 60
SUGGESTION_POST_LIMIT = 30


def _branch_rank(branch_id: str) -> int:
    return 0 if normalize_branch_id(branch_id) == DEFAULT_BRANCH_ID else 1


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
    target_user: models.User,
    target_agent: models.Agent,
    days: int,
    branch_id: str,
) -> str:
    """Collect bounded user-owned text for suggestion generation."""
    since = utc_now_seconds() - timedelta(days=days)
    normalized_branch_id = normalize_branch_id(branch_id)
    read_windows = get_branch_read_windows(db, normalized_branch_id, utc_now_seconds())
    sections: list[str] = []

    autobiography = str(target_user.autobiography or "").strip()
    if autobiography:
        sections.append(f"【数字自传】\n{autobiography[:5000]}")

    chat_rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.agent_id == target_agent.id,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            models.ChatLog.timestamp >= since,
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .limit(SUGGESTION_CHAT_LIMIT)
        .all()
    )
    if chat_rows:
        chat_lines = []
        ordered_chat_rows = sorted(
            chat_rows,
            key=lambda row: (row.timestamp, _branch_rank(row.branch_id), row.id),
        )
        for row in ordered_chat_rows:
            timestamp = row.timestamp.isoformat(sep=" ") if row.timestamp else ""
            chat_lines.append(
                (
                    f"[{timestamp}] 用户：{row.user_message}\n"
                    f"[{timestamp}] Agent：{row.agent_reply}"
                )[:1600],
            )
        sections.append("【近期私聊】\n" + "\n".join(chat_lines))

    post_events = (
        db.query(models.EventLog)
        .filter(
            models.EventLog.agent_id == target_agent.id,
            branch_window_filter(
                models.EventLog.branch_id,
                models.EventLog.timestamp,
                models.EventLog.event_id,
                read_windows,
            ),
            models.EventLog.event_type == "POST_CREATED",
            models.EventLog.timestamp >= since,
        )
        .order_by(models.EventLog.timestamp.desc(), models.EventLog.event_id.desc())
        .limit(SUGGESTION_POST_LIMIT)
        .all()
    )
    if post_events:
        post_lines = []
        ordered_post_events = sorted(
            post_events,
            key=lambda row: (row.timestamp, _branch_rank(row.branch_id), row.event_id),
        )
        for row in ordered_post_events:
            payload = row.payload if isinstance(row.payload, dict) else {}
            content = str(payload.get("content") or "").strip()
            if not content and payload.get("post_id") is not None:
                post = db.get(models.Post, payload.get("post_id"))
                content = str(post.content if post is not None else "").strip()
            if not content:
                continue
            timestamp = row.timestamp.isoformat(sep=" ") if row.timestamp else ""
            post_lines.append(f"[{timestamp}] {content[:800]}")
        if post_lines:
            sections.append("【近期广场帖子】\n" + "\n".join(post_lines))

    return "\n\n".join(sections)


@router.get(
    "/suggestions",
    response_model=list[CounterfactualSuggestion],
)
async def suggest_counterfactual_decision_points(
    days: int = Query(90, ge=1, le=365),
    branch_id: str = Query("main", min_length=1, max_length=128),
    agent_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[CounterfactualSuggestion]:
    """Suggest key life decision points from autobiography and recent records."""
    target_agent, target_user = resolve_target_user_for_agent(
        db,
        current_user,
        agent_id,
    )
    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    context = _build_suggestion_context(
        db,
        target_user,
        target_agent,
        days,
        normalized_branch_id,
    )
    if not context.strip():
        return []

    return [
        CounterfactualSuggestion(**suggestion)
        for suggestion in await suggest_counterfactual_anchors(context)
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
    target_agent, target_user = resolve_target_user_for_agent(
        db,
        current_user,
        anchor.agent_id,
    )
    branch_id = normalize_branch_id(anchor.branch_id)
    if not branch_exists(db, branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )
    timestamp = utc_now_seconds()
    anchor_memory = _format_anchor_memory(anchor)
    if branch_id == DEFAULT_BRANCH_ID:
        core_memory = normalize_core_memory(target_user.core_memory)
    else:
        reconstructed_state = TimeMachine(db).reconstruct_state(
            agent_id=target_agent.id,
            target_timestamp=timestamp,
            branch_id=branch_id,
        )
        core_memory = normalize_core_memory(reconstructed_state.get("core_memory"))
    payload = {
        "user_id": target_user.id,
        "branch_id": branch_id,
        "decision_context": anchor.decision_context,
        "actual_choice": anchor.actual_choice,
        "actual_result": anchor.actual_result,
        "counterfactual_action": anchor.counterfactual_action,
        "counterfactual_result": anchor.counterfactual_result,
        "description": anchor_memory,
    }

    append_event(
        db,
        agent_id=target_agent.id,
        branch_id=branch_id,
        event_type="COUNTERFACTUAL_ANCHOR_CREATED",
        payload=payload,
        timestamp=timestamp,
        commit=False,
    )

    existing_traits = core_memory["persona_traits"].strip()
    anchor_line = f"- {anchor_memory}"
    if anchor_memory not in existing_traits:
        core_memory["persona_traits"] = (
            f"{existing_traits}\n{anchor_line}".strip()
        )[-8000:]

    if branch_id == DEFAULT_BRANCH_ID:
        target_user.core_memory = core_memory
    append_event(
        db,
        agent_id=target_agent.id,
        branch_id=branch_id,
        event_type="CORE_MEMORY_UPDATED",
        payload={
            "source": "counterfactual_anchor",
            "user_id": target_user.id,
            "key": "persona_traits",
            "appended_anchor": anchor_memory,
            "core_memory": core_memory,
        },
        timestamp=timestamp,
        commit=False,
    )

    db.commit()
    db.refresh(target_user)
    return CounterfactualSubmitResponse(saved=True, core_memory_updated=True)
