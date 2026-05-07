"""Private daily sync chat endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import chat as chat_crud
from app.database import get_db
from app import models
from app.schemas.chat import ChatMessageCreate, ChatReplyOut
from app.security import get_current_user
from app.services.llm_service import chat_with_agent


router = APIRouter(tags=["chat"])


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

    agent_reply, memory_chunks_used = chat_with_agent(db_agent, chat_in.message)
    chat_log = chat_crud.create_chat_log(
        db=db,
        agent_id=db_agent.id,
        user_message=chat_in.message,
        agent_reply=agent_reply,
    )
    return ChatReplyOut(
        reply=agent_reply,
        chat_log=chat_log,
        memory_chunks_used=memory_chunks_used,
    )
