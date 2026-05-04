"""RESTful user endpoints for registration and questionnaire submission."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import user as user_crud
from app.database import get_db
from app.schemas.agent import AgentOut
from app.schemas.user import (
    QuestionnaireCreate,
    QuestionnaireSubmissionOut,
    UserCreate,
    UserOut,
)


router = APIRouter(prefix="/api/users", tags=["users"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
def register_user(user_in: UserCreate, db: Session = Depends(get_db)) -> UserOut:
    """Register a new user and store only the bcrypt password hash."""
    existing_user = user_crud.get_user_by_username(db, user_in.username)
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is already registered.",
        )

    return user_crud.create_user(db, user_in)


@router.post(
    "/{user_id}/questionnaire",
    response_model=QuestionnaireSubmissionOut,
    status_code=status.HTTP_200_OK,
)
def submit_questionnaire(
    user_id: int,
    questionnaire_in: QuestionnaireCreate,
    db: Session = Depends(get_db),
) -> QuestionnaireSubmissionOut:
    """Save questionnaire data and create or update the user's virtual agent."""
    db_user = user_crud.get_user(db, user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    updated_user = user_crud.update_user_questionnaire(db, db_user, questionnaire_in)
    db_agent = agent_crud.create_or_update_agent_for_user(db, updated_user)
    return QuestionnaireSubmissionOut(user=updated_user, agent=db_agent)


@router.get("/{user_id}/agent", response_model=AgentOut)
def get_user_agent(user_id: int, db: Session = Depends(get_db)) -> AgentOut:
    """Return the virtual agent associated with a user."""
    db_user = user_crud.get_user(db, user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    db_agent = agent_crud.get_agent_by_user_id(db, user_id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found for this user.",
        )

    return db_agent
