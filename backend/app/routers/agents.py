"""RESTful Agent management endpoints."""

from dataclasses import asdict
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import user as user_crud
from app.database import get_db
from app.schemas.agent import AgentDeletionOut
from app.security import verify_access_token
from app.services.agent_cleanup_service import delete_agent_and_traces


router = APIRouter(prefix="/api/agents", tags=["agents"])


def _admin_key_is_valid(x_loop_admin_key: str | None) -> bool:
    configured_key = os.getenv("LOOP_ADMIN_API_KEY")
    if not configured_key or not x_loop_admin_key:
        return False
    return hmac.compare_digest(x_loop_admin_key, configured_key)


def _optional_current_user_id(
    authorization: str | None,
    db: Session,
) -> int | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        payload = verify_access_token(token.strip())
        user_id = int(payload["sub"])
    except Exception:
        return None
    return user_id if user_crud.get_user(db, user_id) is not None else None


@router.delete("/{agent_id}", response_model=AgentDeletionOut)
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_loop_admin_key: str | None = Header(default=None),
) -> AgentDeletionOut:
    """Delete one Agent and cascade-clean its durable history and memories."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    is_admin = _admin_key_is_valid(x_loop_admin_key)
    current_user_id = _optional_current_user_id(authorization, db)
    if not is_admin and current_user_id != db_agent.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only this Agent's owner or an admin can delete it.",
        )

    try:
        summary = delete_agent_and_traces(db, db_agent)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete Agent and associated data.",
        ) from exc
    return AgentDeletionOut(**asdict(summary))
