"""RESTful user endpoints for registration and questionnaire submission."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import agent as agent_crud
from app.crud import user as user_crud
from app.database import get_db
from app.schemas.agent import AgentOut
from app.schemas.user import (
    AgentSessionChoiceOut,
    AuthSessionOut,
    QuestionnaireCreate,
    QuestionnaireSubmissionOut,
    UserCreate,
    UserLogin,
    UserOut,
)
from app.security import (
    TOKEN_TTL_SECONDS,
    create_access_token,
    get_current_user,
    require_admin_key,
    require_same_user,
)


router = APIRouter(prefix="/api/users", tags=["users"])


@router.post(
    "/register",
    response_model=AuthSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def register_user(user_in: UserCreate, db: Session = Depends(get_db)) -> AuthSessionOut:
    """Register a new user and return a signed bearer session."""
    existing_user = user_crud.get_user_by_username(db, user_in.username)
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is already registered.",
        )

    db_user = user_crud.create_user(db, user_in)
    return AuthSessionOut(
        user=db_user,
        access_token=create_access_token(db_user),
        expires_in=TOKEN_TTL_SECONDS,
    )


@router.post("/login", response_model=AuthSessionOut)
def login_user(user_in: UserLogin, db: Session = Depends(get_db)) -> AuthSessionOut:
    """Authenticate an existing user and return a signed bearer session."""
    db_user = user_crud.get_user_by_username(db, user_in.username)
    if db_user is None or not user_crud.verify_password(
        user_in.password,
        db_user.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthSessionOut(
        user=db_user,
        access_token=create_access_token(db_user),
        expires_in=TOKEN_TTL_SECONDS,
    )


@router.get("/me", response_model=UserOut)
def get_me(current_user=Depends(get_current_user)) -> UserOut:
    """Return the authenticated user for client-side session checks."""
    return current_user


@router.get("/agent-choices", response_model=list[AgentSessionChoiceOut])
def list_agent_session_choices(
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> list[AgentSessionChoiceOut]:
    """List existing agents for controlled research-session switching."""
    agents = agent_crud.get_agents(db)
    return [
        AgentSessionChoiceOut(user=agent.user, agent=agent)
        for agent in agents
        if agent.user is not None
    ]


@router.post("/agent-choices/{agent_id}/session", response_model=AuthSessionOut)
def create_agent_session_choice(
    agent_id: int,
    db: Session = Depends(get_db),
    _admin_key: None = Depends(require_admin_key),
) -> AuthSessionOut:
    """Create a bearer session for the user that owns an existing agent."""
    db_agent = agent_crud.get_agent(db, agent_id)
    if db_agent is None or db_agent.user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )

    return AuthSessionOut(
        user=db_agent.user,
        access_token=create_access_token(db_agent.user),
        expires_in=TOKEN_TTL_SECONDS,
    )


@router.post(
    "/me/questionnaire",
    response_model=QuestionnaireSubmissionOut,
    status_code=status.HTTP_200_OK,
)
def submit_my_questionnaire(
    questionnaire_in: QuestionnaireCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> QuestionnaireSubmissionOut:
    """Save questionnaire data for the authenticated user."""
    updated_user = user_crud.update_user_questionnaire(
        db,
        current_user,
        questionnaire_in,
    )
    db_agent = agent_crud.create_or_update_agent_for_user(db, updated_user)
    return QuestionnaireSubmissionOut(user=updated_user, agent=db_agent)


@router.post(
    "/{user_id}/questionnaire",
    response_model=QuestionnaireSubmissionOut,
    status_code=status.HTTP_200_OK,
)
def submit_questionnaire(
    user_id: int,
    questionnaire_in: QuestionnaireCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> QuestionnaireSubmissionOut:
    """Save questionnaire data and create or update the user's virtual agent."""
    require_same_user(user_id, current_user)
    db_user = user_crud.get_user(db, user_id)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    updated_user = user_crud.update_user_questionnaire(db, db_user, questionnaire_in)
    db_agent = agent_crud.create_or_update_agent_for_user(db, updated_user)
    return QuestionnaireSubmissionOut(user=updated_user, agent=db_agent)


@router.get("/me/agent", response_model=AgentOut)
def get_my_agent(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AgentOut:
    """Return the virtual agent associated with the authenticated user."""
    db_agent = agent_crud.get_agent_by_user_id(db, current_user.id)
    if db_agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found for this user.",
        )

    return db_agent


@router.get("/{user_id}/agent", response_model=AgentOut)
def get_user_agent(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AgentOut:
    """Return the virtual agent associated with a user."""
    require_same_user(user_id, current_user)
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
