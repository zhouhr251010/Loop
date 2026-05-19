"""Private daily sync chat endpoints."""

import logging
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import chat as chat_crud
from app.database import get_db
from app import models
from app.models import utc_now_seconds
from app.schemas.chat import (
    ChatLogOut,
    ChatMemoryDiagnostic,
    ChatMessageCreate,
    ChatReplyOut,
    ChatSessionOut,
    DriftCheckCreate,
    DriftCheckOut,
)
from app.security import get_current_user
from app.services.branching import (
    branch_exists,
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.core_memory_service import format_core_memory_for_prompt
from app.services.drift_detector import evaluate_drift_zero_shot
from app.services.event_store import append_event
from app.services.llm_service import (
    chat_with_agent,
    chat_with_agent_static_prompt,
    fallback_chat_reply,
)
from app.services.memory_watcher import extract_and_update_memory_background
from app.services.rag_service import retrieve_hybrid_memory
from app.services.time_machine import TimeMachine


router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)
CHAT_LOG_SEPARATOR = "-" * 88
DRIFT_RECENT_REPLY_LIMIT = 5
EXPERIMENT_MODE_ALIASES = {
    "full_iacl": "mode_alpha",
    "static_prompt": "mode_beta",
}


def _normalize_experiment_mode(value: object) -> str:
    mode = str(value or "mode_alpha").strip() or "mode_alpha"
    return EXPERIMENT_MODE_ALIASES.get(mode, mode)


def _normalize_session_id(value: object) -> str:
    return str(value or "default_session").strip()[:64] or "default_session"


def _normalize_topic(value: object) -> str:
    return str(value or "general").strip()[:64] or "general"


def _summarize_diagnostic_text(value: object, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


async def _build_memory_diagnostics(
    db: Session,
    db_agent: models.Agent,
    current_user: models.User,
    message: str,
    branch_id: str,
    experiment_mode: str,
    reconstructed_core_memory: str | None = None,
    retrieved_memories: list[str] | None = None,
) -> list[ChatMemoryDiagnostic]:
    """Collect a small debug-only snapshot of identity/RAG context."""
    if experiment_mode == "mode_beta":
        return []

    diagnostics: list[ChatMemoryDiagnostic] = []
    identity_core = (
        reconstructed_core_memory
        if reconstructed_core_memory
        else format_core_memory_for_prompt(current_user.core_memory)
    )
    identity_summary = _summarize_diagnostic_text(identity_core)
    if identity_summary:
        diagnostics.append(
            ChatMemoryDiagnostic(kind="identity", summary=identity_summary),
        )

    if retrieved_memories is None:
        try:
            retrieved_memories = await retrieve_hybrid_memory(
                user_id=current_user.id,
                query=message,
                top_k=3,
                branch_id=branch_id,
                source="chat_diagnostics",
                agent_id=db_agent.id,
            )
        except Exception as exc:
            logger.warning(
                "[Chat Diagnostics] RAG diagnostic retrieval failed for agent_id=%s: %s",
                db_agent.id,
                exc,
            )
            retrieved_memories = []

    for memory in retrieved_memories[:5]:
        summary = _summarize_diagnostic_text(memory)
        if not summary:
            continue
        kind = (
            "semantic"
            if "GraphRAG" in summary or "affinity_score" in summary
            else "episodic"
        )
        diagnostics.append(ChatMemoryDiagnostic(kind=kind, summary=summary))

    return diagnostics[:6]


def _log_chat_warning(context: str, exc: Exception) -> None:
    print(
        f"[Loop Chat] {context}: {exc.__class__.__name__}: {exc}",
        flush=True,
    )


async def _create_chat_reply(
    db: Session,
    db_agent: models.Agent,
    chat_in: ChatMessageCreate,
    current_user: models.User,
    background_tasks: BackgroundTasks | None = None,
) -> ChatReplyOut:
    chat_turn_id = uuid4().hex[:8]
    branch_id = normalize_branch_id(chat_in.branch_id)
    session_id = _normalize_session_id(chat_in.session_id)
    topic = _normalize_topic(chat_in.topic)
    experiment_mode = _normalize_experiment_mode(chat_in.experiment_mode)
    query_route = (
        "Static Baseline" if experiment_mode == "mode_beta" else "Full IACL"
    )
    user_id = getattr(db_agent, "user_id", "unknown")
    message_preview = chat_in.message.replace("\n", " ")[:80]
    logger.info(
        "%s\n[Chat Turn Start] turn_id=%s user_id=%s agent_id=%s "
        "branch_id=%s session_id=%s topic=%s model=%s "
        "experiment_mode=%s message=%r\n%s",
        CHAT_LOG_SEPARATOR,
        chat_turn_id,
        user_id,
        db_agent.id,
        branch_id,
        session_id,
        topic,
        chat_in.model,
        experiment_mode,
        message_preview,
        CHAT_LOG_SEPARATOR,
    )
    branch_is_known = branch_exists(db, branch_id)

    try:
        reconstructed_core_memory = None
        if (
            experiment_mode == "mode_alpha"
            and branch_id != "main"
            and branch_is_known
        ):
            reconstructed_state = TimeMachine(db).reconstruct_state(
                agent_id=db_agent.id,
                target_timestamp=utc_now_seconds(),
                branch_id=branch_id,
            )
            reconstructed_core_memory = str(
                reconstructed_state.get("current_core_memory") or "",
            ).strip()

        warning = None
        retrieved_memories: list[str] = []
        if experiment_mode == "mode_beta":
            agent_reply, retrieved_memories = await chat_with_agent_static_prompt(
                db_agent,
                chat_in.message,
                chat_model=chat_in.model,
            )
        else:
            recent_history = chat_crud.get_recent_chat_logs(
                db=db,
                agent_id=db_agent.id,
                branch_id=branch_id,
                session_id=session_id,
                topic=topic,
                limit=chat_crud.RECENT_CHAT_HISTORY_TURNS,
            )

            def load_historical_chat_logs(lookback_turns: int) -> list[models.ChatLog]:
                return chat_crud.get_historical_chat_logs(
                    db=db,
                    agent_id=db_agent.id,
                    branch_id=branch_id,
                    session_id=session_id,
                    topic=topic,
                    lookback_turns=lookback_turns,
                    skip_recent_turns=chat_crud.RECENT_CHAT_HISTORY_TURNS,
                )

            try:
                agent_reply, retrieved_memories = await chat_with_agent(
                    db_agent,
                    chat_in.message,
                    chat_model=chat_in.model,
                    branch_id=branch_id,
                    reconstructed_core_memory=reconstructed_core_memory,
                    recent_history=recent_history,
                    historical_chat_loader=load_historical_chat_logs,
                    session_id=session_id,
                    topic=topic,
                )
            except Exception as exc:
                _log_chat_warning("chat generation failed after service fallback", exc)
                agent_reply, retrieved_memories = await fallback_chat_reply(
                    db_agent,
                    chat_in.message,
                    branch_id=branch_id,
                )
                warning = (
                    "Remote model is unavailable; returned a memory-based fallback."
                )

        try:
            memory_chunks_used = len(retrieved_memories)
            chat_log = chat_crud.create_chat_log(
                db=db,
                agent_id=db_agent.id,
                user_message=chat_in.message,
                agent_reply=agent_reply,
                branch_id=branch_id,
                session_id=session_id,
                experiment_mode=experiment_mode,
                topic=topic,
                session_type=chat_in.session_type,
            )
            chat_log_out = ChatLogOut.model_validate(chat_log)
            chat_log_out.branch_id = branch_id
            chat_log_out.session_id = session_id
            chat_log_out.experiment_mode = experiment_mode
            chat_log_out.topic = topic
            chat_log_out.session_type = chat_in.session_type
        except Exception as exc:
            logger.exception(
                "[Chat Turn Error] turn_id=%s storage or response shaping failed",
                chat_turn_id,
            )
            return ChatReplyOut(
                reply=agent_reply,
                chat_log=None,
                memory_chunks_used=memory_chunks_used,
                model_used=chat_in.model,
                stored=False,
                warning=warning or "Reply was generated but could not be stored.",
                query_route=query_route,
                memory_diagnostics=await _build_memory_diagnostics(
                    db=db,
                    db_agent=db_agent,
                    current_user=current_user,
                    message=chat_in.message,
                    branch_id=branch_id,
                    experiment_mode=experiment_mode,
                    reconstructed_core_memory=reconstructed_core_memory,
                    retrieved_memories=retrieved_memories,
                ),
            )

        if background_tasks is not None:
            background_tasks.add_task(
                extract_and_update_memory_background,
                current_user.id,
                db_agent.id,
                branch_id,
                session_id,
                chat_in.message,
                agent_reply,
            )

        return ChatReplyOut(
            reply=agent_reply,
            chat_log=chat_log_out,
            memory_chunks_used=memory_chunks_used,
            model_used=chat_in.model,
            stored=True,
            warning=warning,
            query_route=query_route,
            memory_diagnostics=await _build_memory_diagnostics(
                db=db,
                db_agent=db_agent,
                current_user=current_user,
                message=chat_in.message,
                branch_id=branch_id,
                experiment_mode=experiment_mode,
                reconstructed_core_memory=reconstructed_core_memory,
                retrieved_memories=retrieved_memories,
            ),
        )
    except Exception:
        logger.exception("[Chat Turn Error] turn_id=%s failed", chat_turn_id)
        raise
    finally:
        logger.info(
            "%s\n[Chat Turn End] turn_id=%s user_id=%s agent_id=%s\n%s",
            CHAT_LOG_SEPARATOR,
            chat_turn_id,
            user_id,
            db_agent.id,
            CHAT_LOG_SEPARATOR,
        )


@router.post(
    "/api/agents/me/chat",
    response_model=ChatReplyOut,
    status_code=status.HTTP_201_CREATED,
)
async def chat_with_my_agent_endpoint(
    chat_in: ChatMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ChatReplyOut:
    """Send a private sync message to the authenticated user's Agent."""
    db_agent = agent_crud.get_agent_by_user_id(db, current_user.id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found for this user.",
        )

    return await _create_chat_reply(
        db,
        db_agent,
        chat_in,
        current_user,
        background_tasks,
    )


@router.get(
    "/api/agents/{agent_id}/chat",
    response_model=list[ChatLogOut],
)
def get_agent_chat_history(
    agent_id: int,
    branch_id: str = "main",
    session_id: str = Query("default_session", min_length=1, max_length=64),
    skip: int = Query(0, ge=0),
    limit: int = Query(15, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """Return bounded session history under one branch timeline."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only read chat history for your own agent.",
        )

    normalized_branch_id = normalize_branch_id(branch_id)
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    normalized_session_id = _normalize_session_id(session_id)
    rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.agent_id == agent_id,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            models.ChatLog.session_id == normalized_session_id,
            models.ChatLog.session_type == models.SessionType.HUMAN_TO_AGENT.value,
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    rows = list(reversed(rows))
    return [
        {
            "id": row.id,
            "agent_id": row.agent_id,
            "sender_user_id": row.sender_user_id,
            "receiver_user_id": row.receiver_user_id,
            "group_id": row.group_id,
            "user_message": row.user_message,
            "agent_reply": row.agent_reply,
            "timestamp": row.timestamp,
            "branch_id": row.branch_id,
            "session_id": row.session_id,
            "topic": getattr(row, "topic", "general"),
            "experiment_mode": _normalize_experiment_mode(row.experiment_mode),
            "session_type": row.session_type,
        }
        for row in rows
    ]


@router.get(
    "/api/chat/{agent_id}/sessions",
    response_model=list[ChatSessionOut],
)
def get_agent_chat_sessions(
    agent_id: int,
    branch_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> list[ChatSessionOut]:
    """Return latest chat sessions under one branch timeline."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only read chat sessions for your own agent.",
        )

    normalized_branch_id = normalize_branch_id(branch_id)
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    filters = [
        models.ChatLog.agent_id == agent_id,
        branch_window_filter(
            models.ChatLog.branch_id,
            models.ChatLog.timestamp,
            None,
            read_windows,
        ),
        models.ChatLog.session_type == models.SessionType.HUMAN_TO_AGENT.value,
    ]

    latest_rows = (
        db.query(
            models.ChatLog.session_id.label("session_id"),
            func.max(models.ChatLog.id).label("latest_id"),
            func.count(models.ChatLog.id).label("turn_count"),
        )
        .filter(*filters)
        .group_by(models.ChatLog.session_id)
        .subquery()
    )
    rows = (
        db.query(
            latest_rows.c.session_id,
            latest_rows.c.turn_count,
            models.ChatLog.user_message.label("latest_message"),
            models.ChatLog.timestamp.label("latest_timestamp"),
        )
        .join(
            models.ChatLog,
            (models.ChatLog.agent_id == agent_id)
            & (models.ChatLog.session_type == models.SessionType.HUMAN_TO_AGENT.value)
            & (models.ChatLog.session_id == latest_rows.c.session_id)
            & (models.ChatLog.id == latest_rows.c.latest_id),
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .limit(limit)
        .all()
    )

    sessions: list[ChatSessionOut] = []
    seen_sessions: set[str] = set()
    for row in rows:
        normalized_session_id = _normalize_session_id(row.session_id)
        if normalized_session_id in seen_sessions:
            continue
        seen_sessions.add(normalized_session_id)

        first_log = (
            db.query(models.ChatLog)
            .filter(
                models.ChatLog.agent_id == agent_id,
                branch_window_filter(
                    models.ChatLog.branch_id,
                    models.ChatLog.timestamp,
                    None,
                    read_windows,
                ),
                models.ChatLog.session_id == normalized_session_id,
                models.ChatLog.session_type == models.SessionType.HUMAN_TO_AGENT.value,
            )
            .order_by(models.ChatLog.timestamp.asc(), models.ChatLog.id.asc())
            .first()
        )
        first_message = first_log.user_message if first_log else row.latest_message
        sessions.append(
            ChatSessionOut(
                branch_id=normalized_branch_id,
                session_id=normalized_session_id,
                first_message=first_message,
                latest_message=row.latest_message,
                latest_timestamp=row.latest_timestamp,
                turn_count=int(row.turn_count or 0),
            ),
        )

    return sessions


@router.post(
    "/api/chat/{agent_id}/check-drift",
    response_model=DriftCheckOut,
)
async def check_agent_identity_drift(
    agent_id: int,
    drift_in: DriftCheckCreate | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> DriftCheckOut:
    """Judge recent Agent replies against the user's current identity core."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only check drift for your own agent.",
        )

    branch_id = normalize_branch_id(drift_in.branch_id if drift_in else "main")
    session_id = _normalize_session_id(
        drift_in.session_id if drift_in else "default_session",
    )
    topic = _normalize_topic(drift_in.topic if drift_in else "general")
    if not branch_exists(db, branch_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branch not found.",
        )

    rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.agent_id == agent_id,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                get_branch_read_windows(db, branch_id),
            ),
            models.ChatLog.session_id == session_id,
            models.ChatLog.topic == topic,
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .limit(DRIFT_RECENT_REPLY_LIMIT)
        .all()
    )
    recent_messages = [
        str(row.agent_reply or "").strip()
        for row in reversed(rows)
        if str(row.agent_reply or "").strip()
    ]

    if branch_id != "main":
        reconstructed_state = TimeMachine(db).reconstruct_state(
            agent_id=agent_id,
            target_timestamp=utc_now_seconds(),
            branch_id=branch_id,
        )
        identity_core = str(
            reconstructed_state.get("current_core_memory") or "",
        ).strip()
    else:
        identity_core = format_core_memory_for_prompt(current_user.core_memory)

    result = await evaluate_drift_zero_shot(
        recent_messages=recent_messages,
        identity_core=identity_core,
    )
    result["drift_probability"] = max(
        0.0,
        min(
            1.0,
            float(
                result.get(
                    "drift_probability",
                    1.0 - result.get("consistency_score", 1.0),
                ),
            ),
        ),
    )

    if result.get("is_drifting") is True:
        append_event(
            db,
            agent_id=agent_id,
            branch_id=branch_id,
            event_type="DRIFT_DETECTED",
            payload={
                "consistency_score": result.get("consistency_score"),
                "drift_probability": result.get("drift_probability"),
                "reason": result.get("reason"),
                "recent_message_count": len(recent_messages),
                "session_id": session_id,
                "topic": topic,
                "source": "zero_shot_judge",
            },
        )

    return DriftCheckOut(**result)


@router.post(
    "/api/agents/{agent_id}/chat",
    response_model=ChatReplyOut,
    status_code=status.HTTP_201_CREATED,
)
async def chat_with_agent_endpoint(
    agent_id: int,
    chat_in: ChatMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> ChatReplyOut:
    """Send a private sync message to an agent and store the chat turn."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    if db_agent.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only chat with your own agent.",
        )

    return await _create_chat_reply(
        db,
        db_agent,
        chat_in,
        current_user,
        background_tasks,
    )
