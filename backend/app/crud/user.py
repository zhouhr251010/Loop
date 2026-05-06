"""Database operations for users and questionnaire profiles."""

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app import models
from app.schemas.user import QuestionnaireCreate, UserCreate


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_user(db: Session, user_id: int) -> models.User | None:
    """Return a user by primary key."""
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> models.User | None:
    """Return a user by username."""
    return db.query(models.User).filter(models.User.username == username).first()


def hash_password(password: str) -> str:
    """Hash a plain-text password before persistence."""
    return pwd_context.hash(password)


def create_user(db: Session, user_in: UserCreate) -> models.User:
    """Create a user with a bcrypt password hash."""
    db_user = models.User(
        username=user_in.username,
        password_hash=hash_password(user_in.password),
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
