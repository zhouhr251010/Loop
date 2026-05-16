"""Database configuration for the Loop research platform."""

from collections.abc import Generator
import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")


def _build_database_url() -> str:
    """Build the required Postgres URL, failing fast if it is not configured."""
    postgres_url = os.getenv("POSTGRES_URL", "").strip()
    if postgres_url:
        return postgres_url

    postgres_user = os.getenv("POSTGRES_USER", "").strip()
    postgres_password = os.getenv("POSTGRES_PASSWORD", "")
    postgres_db = os.getenv("POSTGRES_DB", "").strip()
    if postgres_user and postgres_password and postgres_db:
        postgres_host = os.getenv("POSTGRES_HOST", "127.0.0.1").strip()
        postgres_port = os.getenv("POSTGRES_PORT", "5432").strip()
        return (
            "postgresql+psycopg2://"
            f"{quote_plus(postgres_user)}:{quote_plus(postgres_password)}"
            f"@{postgres_host}:{postgres_port}/{quote_plus(postgres_db)}"
        )

    raise RuntimeError(
        "PostgreSQL is required. Configure POSTGRES_URL or "
        "POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB.",
    )


SQLALCHEMY_DATABASE_URL = _build_database_url()
IS_POSTGRES = SQLALCHEMY_DATABASE_URL.startswith("postgresql")

engine_kwargs = {"pool_pre_ping": True}

engine = create_engine(SQLALCHEMY_DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

DATABASE_INITIALIZE_LOCK_ID = 817504201


def get_db() -> Generator[Session, None, None]:
    """Provide a database session for FastAPI dependencies."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_postgres_extensions() -> None:
    """Enable required Postgres extensions before ORM tables use them."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))


def _postgres_embedding_dim() -> int:
    raw_value = os.getenv("LOOP_EMBEDDING_DIM", "1024").strip()
    try:
        embedding_dim = int(raw_value)
    except ValueError:
        embedding_dim = 1024
    return max(1, min(embedding_dim, 4096))


def ensure_postgres_rag_schema() -> None:
    """Create the stateless pgvector-backed RAG document store."""
    if not IS_POSTGRES:
        return

    embedding_dim = _postgres_embedding_dim()
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding vector({embedding_dim}),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS rag_documents_metadata_idx
                ON rag_documents USING gin (metadata)
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS rag_documents_created_at_idx
                ON rag_documents (created_at DESC)
                """,
            ),
        )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS rag_documents_embedding_idx
                    ON rag_documents
                    USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                    """,
                ),
            )
    except Exception:
        # Small or newly initialized pgvector deployments can still serve exact
        # ORDER BY distance queries without the approximate index.
        pass


def ensure_postgres_event_log_triggers() -> None:
    """Install Postgres triggers that keep event_logs append-only."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION prevent_event_logs_mutation()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'event_logs are append-only';
                END;
                $$ LANGUAGE plpgsql;
                """,
            ),
        )
        connection.execute(text("DROP TRIGGER IF EXISTS event_logs_no_update ON event_logs"))
        connection.execute(text("DROP TRIGGER IF EXISTS event_logs_no_delete ON event_logs"))
        connection.execute(
            text(
                """
                CREATE TRIGGER event_logs_no_update
                BEFORE UPDATE ON event_logs
                FOR EACH ROW
                EXECUTE FUNCTION prevent_event_logs_mutation()
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER event_logs_no_delete
                BEFORE DELETE ON event_logs
                FOR EACH ROW
                EXECUTE FUNCTION prevent_event_logs_mutation()
                """,
            ),
        )


def ensure_agent_npc_schema() -> None:
    """Keep existing databases aligned with the system NPC agent column."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE agents
                ADD COLUMN IF NOT EXISTS is_npc BOOLEAN NOT NULL DEFAULT FALSE
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_agents_is_npc
                ON agents (is_npc)
                """,
            ),
        )


def initialize_database() -> None:
    """Initialize the configured database backend for application startup."""
    if IS_POSTGRES:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": DATABASE_INITIALIZE_LOCK_ID},
            )
            try:
                ensure_postgres_extensions()
                Base.metadata.create_all(bind=engine)
                ensure_agent_npc_schema()
                ensure_postgres_extensions()
                ensure_postgres_rag_schema()
                ensure_postgres_event_log_triggers()
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": DATABASE_INITIALIZE_LOCK_ID},
                )
        return

    ensure_postgres_extensions()
    Base.metadata.create_all(bind=engine)
    ensure_agent_npc_schema()
    ensure_postgres_extensions()
    ensure_postgres_rag_schema()
    ensure_postgres_event_log_triggers()
