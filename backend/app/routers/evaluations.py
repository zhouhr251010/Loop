"""Public blind-test evaluation endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.crud import agent as agent_crud
from app.database import get_db
from app.schemas.evaluation import (
    BlindTestChatLogOut,
    BlindTestOut,
    BlindTestSubmitCreate,
    BlindTestSubmitOut,
)


router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])
BLIND_TEST_SAMPLE_SIZE = 5


@router.get(
    "/blind-test/{agent_id}",
    response_model=BlindTestOut,
)
def get_blind_test_samples(
    agent_id: int,
    db: Session = Depends(get_db),
) -> BlindTestOut:
    """Return a small random blind-test set from one Agent's chat history."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    rows = (
        db.query(models.ChatLog)
        .filter(models.ChatLog.agent_id == agent_id)
        .order_by(func.random())
        .limit(BLIND_TEST_SAMPLE_SIZE)
        .all()
    )

    samples = [
        BlindTestChatLogOut(
            id=row.id,
            user_message=row.user_message,
            agent_reply=row.agent_reply,
            timestamp=row.timestamp,
        )
        for row in rows
    ]
    return BlindTestOut(
        agent_id=db_agent.id,
        agent_name=db_agent.agent_name,
        samples=samples,
    )


@router.post(
    "/blind-test/{agent_id}/submit",
    response_model=BlindTestSubmitOut,
    status_code=status.HTTP_201_CREATED,
)
def submit_blind_test_evaluation(
    agent_id: int,
    evaluation_in: BlindTestSubmitCreate,
    db: Session = Depends(get_db),
) -> BlindTestSubmitOut:
    """Store a public evaluator's authenticity rating for one Agent."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    db_evaluation = models.Evaluation(
        agent_id=agent_id,
        evaluator_relation=evaluation_in.evaluator_relation,
        authenticity_score=evaluation_in.authenticity_score,
        qualitative_feedback=evaluation_in.qualitative_feedback.strip(),
        sampled_chat_log_ids=evaluation_in.sampled_chat_log_ids,
    )
    db.add(db_evaluation)
    db.commit()
    db.refresh(db_evaluation)
    return BlindTestSubmitOut.model_validate(db_evaluation)
