"""Private daily sync chat endpoints."""

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import chat as chat_crud
from app.database import get_db
from app import models
from app.schemas.chat import ChatLogOut, ChatMessageCreate, ChatReplyOut
from app.security import get_current_user
from app.services.llm_service import chat_with_agent, fallback_chat_reply


router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)
CHAT_LOG_SEPARATOR = "-" * 88


def _log_chat_warning(context: str, exc: Exception) -> None:
    print(
        f"[Loop Chat] {context}: {exc.__class__.__name__}: {exc}",
        flush=True,
    )


def _create_chat_reply(
    db: Session,
    db_agent: models.Agent,
    chat_in: ChatMessageCreate,
) -> ChatReplyOut:
    chat_turn_id = uuid4().hex[:8]
    user_id = getattr(db_agent, "user_id", "unknown")
    message_preview = chat_in.message.replace("\n", " ")[:80]
    logger.info(
        "%s\n[Chat Turn Start] turn_id=%s user_id=%s agent_id=%s "
        "model=%s message=%r\n%s",
        CHAT_LOG_SEPARATOR,
        chat_turn_id,
        user_id,
        db_agent.id,
        chat_in.model,
        message_preview,
        CHAT_LOG_SEPARATOR,
    )
    try:
        warning = None
        try:
            agent_reply, memory_chunks_used = chat_with_agent(
                db_agent,
                chat_in.message,
                chat_model=chat_in.model,
            )
        except Exception as exc:
            _log_chat_warning("chat generation failed after service fallback", exc)
            agent_reply, memory_chunks_used = fallback_chat_reply(
                db_agent,
                chat_in.message,
            )
            warning = "Remote model is unavailable; returned a memory-based fallback."

        try:
            chat_log = chat_crud.create_chat_log(
                db=db,
                agent_id=db_agent.id,
                user_message=chat_in.message,
                agent_reply=agent_reply,
            )
            chat_log_out = ChatLogOut.model_validate(chat_log)
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
            )

        return ChatReplyOut(
            reply=agent_reply,
            chat_log=chat_log_out,
            memory_chunks_used=memory_chunks_used,
            model_used=chat_in.model,
            stored=True,
            warning=warning,
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
def chat_with_my_agent_endpoint(
    chat_in: ChatMessageCreate,
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

    return _create_chat_reply(db, db_agent, chat_in)


@router.post(
    "/api/agents/{agent_id}/chat",
    response_model=ChatReplyOut,
    status_code=status.HTTP_201_CREATED,
)
def chat_with_agent_endpoint(
    agent_id: int,
    chat_in: ChatMessageCreate,
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

    return _create_chat_reply(db, db_agent, chat_in)
