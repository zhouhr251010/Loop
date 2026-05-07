"""Database operations for users and questionnaire profiles."""

import bcrypt

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.schemas.user import QuestionnaireCreate, UserCreate
from app.services.core_memory_service import DEFAULT_CORE_MEMORY


def get_user(db: Session, user_id: int) -> models.User | None:
    """Return a user by primary key."""
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> models.User | None:
    """Return a user by username."""
    return (
        db.query(models.User)
        .filter(func.lower(models.User.username) == username.lower())
        .first()
    )


def hash_password(password: str) -> str:
    """Hash a plain-text password before persistence."""
    password_bytes = password.encode("utf-8")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether a plain-text password matches the stored hash."""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except ValueError:
        return False


def create_user(db: Session, user_in: UserCreate) -> models.User:
    """Create a user with a bcrypt password hash."""
    db_user = models.User(
        username=user_in.username,
        password_hash=hash_password(user_in.password),
        core_memory=DEFAULT_CORE_MEMORY.copy(),
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def update_user_questionnaire(
    db: Session,
    user: models.User,
    questionnaire_in: QuestionnaireCreate,
) -> models.User:
    """Persist questionnaire fields on an existing user."""
    user.mbti_type = questionnaire_in.mbti_type
    user.big_five_scores = questionnaire_in.big_five_scores
    user.schwartz_values = questionnaire_in.schwartz_values
    user.autobiography = questionnaire_in.autobiography
    db.commit()
    db.refresh(user)
    return user
