"""Database configuration for the Loop research platform."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "loop_research.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Provide a database session for FastAPI dependencies."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_sqlite_schema() -> None:
    """Apply small SQLite schema upgrades that create_all cannot perform."""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    with engine.begin() as connection:
        if "users" in table_names:
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "autobiography" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN autobiography TEXT"))
            if "core_memory" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN core_memory JSON"))

        if "chat_logs" in table_names:
            chat_log_columns = {
                column["name"] for column in inspector.get_columns("chat_logs")
            }
            if "branch_id" not in chat_log_columns:
                connection.execute(
                    text(
                        "ALTER TABLE chat_logs "
                        "ADD COLUMN branch_id VARCHAR(128) NOT NULL DEFAULT 'main'",
                    ),
                )

        if "event_logs" in table_names:
            connection.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS event_logs_no_update
                    BEFORE UPDATE ON event_logs
                    BEGIN
                        SELECT RAISE(ABORT, 'event_logs are append-only');
                    END
                    """,
                ),
            )
            connection.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS event_logs_no_delete
                    BEFORE DELETE ON event_logs
                    BEGIN
                        SELECT RAISE(ABORT, 'event_logs are append-only');
                    END
                    """,
                ),
            )
