"""RESTful Agent management endpoints."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.database import get_db
from app import models
from app.schemas.agent import AgentDeletionOut
from app.security import get_current_user, require_admin
from app.services.agent_cleanup_service import delete_agent_and_traces


router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/directory", response_model=list[dict[str, str]])
def list_agents_directory(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
) -> list[dict[str, str]]:
    """Return a lightweight directory of all Agents for admin simulations."""
    agents = agent_crud.get_agents(db)
    return [
        {
            "agent_id": str(agent.id),
            "name": agent.agent_name,
        }
        for agent in agents
    ]


@router.delete("/{agent_id}", response_model=AgentDeletionOut)
def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
) -> AgentDeletionOut:
    """Delete one Agent and cascade-clean its durable history and memories."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    if not current_user.is_admin and current_user.id != db_agent.user_id:
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
