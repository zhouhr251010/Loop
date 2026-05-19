"""Database configuration for the Loop research platform."""

from collections.abc import Generator
import logging
import os
from pathlib import Path
import re
from urllib.parse import quote_plus

import bcrypt
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
logger = logging.getLogger(__name__)


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


def ensure_probe_response_branch_schema() -> None:
    """Keep existing probe response tables aligned with branch-aware collection."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE probe_responses
                ADD COLUMN IF NOT EXISTS branch_id VARCHAR(128) NOT NULL DEFAULT 'main'
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_probe_responses_branch_id
                ON probe_responses (branch_id)
                """,
            ),
        )


def ensure_chat_log_session_type_schema() -> None:
    """Keep existing chat logs aligned with multi-party session typing."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE chat_logs
                ADD COLUMN IF NOT EXISTS session_type VARCHAR(32)
                NOT NULL DEFAULT 'Human_to_Agent'
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_session_type
                ON chat_logs (session_type)
                """,
            ),
        )


def ensure_chat_log_human_peer_schema() -> None:
    """Keep chat logs aligned with human-to-human sender/receiver metadata."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE chat_logs
                ADD COLUMN IF NOT EXISTS sender_user_id INTEGER
                REFERENCES users(id)
                """,
            ),
        )
        connection.execute(
            text(
                """
                ALTER TABLE chat_logs
                ADD COLUMN IF NOT EXISTS receiver_user_id INTEGER
                REFERENCES users(id)
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_sender_user_id
                ON chat_logs (sender_user_id)
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_receiver_user_id
                ON chat_logs (receiver_user_id)
                """,
            ),
        )


def ensure_chat_log_memory_extraction_schema() -> None:
    """Keep chat logs aligned with bypass memory watcher processing flags."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE chat_logs
                ADD COLUMN IF NOT EXISTS is_memory_extracted BOOLEAN
                NOT NULL DEFAULT TRUE
                """,
            ),
        )
        connection.execute(
            text(
                """
                UPDATE chat_logs
                SET is_memory_extracted = TRUE
                WHERE is_memory_extracted IS NULL
                """,
            ),
        )
        connection.execute(
            text(
                """
                ALTER TABLE chat_logs
                ALTER COLUMN is_memory_extracted SET DEFAULT FALSE
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_is_memory_extracted
                ON chat_logs (is_memory_extracted)
                """,
            ),
        )


def ensure_chat_log_read_schema() -> None:
    """Keep human-to-human chat logs aligned with offline read tracking."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE chat_logs
                ADD COLUMN IF NOT EXISTS is_read BOOLEAN
                NOT NULL DEFAULT FALSE
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_is_read
                ON chat_logs (is_read)
                """,
            ),
        )


def ensure_chat_log_group_schema() -> None:
    """Keep existing chat logs aligned with N-to-N group room messages."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE chat_logs
                ADD COLUMN IF NOT EXISTS group_id VARCHAR(36)
                REFERENCES groups(id)
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_group_id
                ON chat_logs (group_id)
                """,
            ),
        )


def ensure_group_owner_schema() -> None:
    """Keep group rooms aligned with owner metadata."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE groups
                ADD COLUMN IF NOT EXISTS owner_id INTEGER
                REFERENCES users(id)
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_groups_owner_id
                ON groups (owner_id)
                """,
            ),
            )


def ensure_group_summary_branch_schema() -> None:
    """Keep rolling group summaries isolated by world-line branch."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE group_summaries
                ADD COLUMN IF NOT EXISTS branch_id VARCHAR(128) NOT NULL DEFAULT 'main'
                """,
            ),
        )
        connection.execute(
            text(
                """
                UPDATE group_summaries
                SET branch_id = 'main'
                WHERE branch_id IS NULL OR btrim(branch_id) = ''
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_group_summaries_branch_id
                ON group_summaries (branch_id)
                """,
            ),
        )
        connection.execute(
            text(
                """
                DO $$
                DECLARE
                    constraint_name TEXT;
                BEGIN
                    FOR constraint_name IN
                        SELECT con.conname
                        FROM pg_constraint con
                        JOIN pg_class rel ON rel.oid = con.conrelid
                        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                        WHERE rel.relname = 'group_summaries'
                          AND nsp.nspname = current_schema()
                          AND con.contype = 'u'
                          AND pg_get_constraintdef(con.oid) = 'UNIQUE (group_id)'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE group_summaries DROP CONSTRAINT %I',
                            constraint_name
                        );
                    END LOOP;
                END $$;
                """,
            ),
        )
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint con
                        JOIN pg_class rel ON rel.oid = con.conrelid
                        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                        WHERE rel.relname = 'group_summaries'
                          AND nsp.nspname = current_schema()
                          AND con.conname = 'uix_group_summary_branch'
                    ) THEN
                        ALTER TABLE group_summaries
                        ADD CONSTRAINT uix_group_summary_branch
                        UNIQUE (group_id, branch_id);
                    END IF;
                END $$;
                """,
            ),
        )


def ensure_social_chat_indexes_schema() -> None:
    """Keep high-volume social chat reads on bounded composite indexes."""
    if not IS_POSTGRES:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_h2h_thread_page
                ON chat_logs (
                    session_type,
                    branch_id,
                    sender_user_id,
                    receiver_user_id,
                    timestamp DESC,
                    id DESC
                )
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_h2h_unread
                ON chat_logs (
                    session_type,
                    receiver_user_id,
                    is_read,
                    sender_user_id
                )
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_chat_logs_group_page
                ON chat_logs (
                    session_type,
                    group_id,
                    branch_id,
                    timestamp DESC,
                    id DESC
                )
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_group_members_user_lookup
                ON group_members (entity_type, entity_id, group_id)
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_group_members_group_lookup
                ON group_members (group_id, entity_type)
                """,
            ),
        )


def ensure_user_admin_schema() -> None:
    """Keep existing user tables aligned with the configured admin account."""
    if not IS_POSTGRES:
        return

    admin_username = os.getenv("LOOP_ADMIN_USERNAME", "").strip()
    admin_password = os.getenv("LOOP_ADMIN_PASSWORD", "")
    if bool(admin_username) != bool(admin_password):
        raise RuntimeError(
            "Configure both LOOP_ADMIN_USERNAME and LOOP_ADMIN_PASSWORD, or neither.",
        )
    if admin_username:
        if len(admin_username) < 3 or len(admin_username) > 64:
            raise RuntimeError("LOOP_ADMIN_USERNAME must be 3-64 characters.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", admin_username):
            raise RuntimeError(
                "LOOP_ADMIN_USERNAME may only contain letters, numbers, dots, "
                "underscores, and hyphens.",
            )
        if len(admin_password) < 8:
            raise RuntimeError("LOOP_ADMIN_PASSWORD must be at least 8 characters.")
        if len(admin_password.encode("utf-8")) > 72:
            raise RuntimeError("LOOP_ADMIN_PASSWORD must be 72 UTF-8 bytes or fewer.")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE
                """,
            ),
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_users_is_admin
                ON users (is_admin)
                """,
            ),
        )
        if admin_username:
            password_hash = bcrypt.hashpw(
                admin_password.encode("utf-8"),
                bcrypt.gensalt(),
            ).decode("utf-8")
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET is_admin = FALSE
                    WHERE is_admin = TRUE
                      AND lower(username) != lower(:admin_username)
                    """,
                ),
                {"admin_username": admin_username},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (username, password_hash, is_admin, created_at)
                    VALUES (:admin_username, :password_hash, TRUE, CURRENT_TIMESTAMP(0))
                    ON CONFLICT (username) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        is_admin = TRUE
                    """,
                ),
                {
                    "admin_username": admin_username,
                    "password_hash": password_hash,
                },
            )
        else:
            logger.warning(
                "LOOP_ADMIN_USERNAME/LOOP_ADMIN_PASSWORD are not configured; "
                "no bearer-login admin account will be available.",
            )
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET is_admin = FALSE
                    WHERE is_admin = TRUE
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
                ensure_probe_response_branch_schema()
                ensure_chat_log_session_type_schema()
                ensure_chat_log_human_peer_schema()
                ensure_chat_log_memory_extraction_schema()
                ensure_chat_log_read_schema()
                ensure_chat_log_group_schema()
                ensure_group_owner_schema()
                ensure_group_summary_branch_schema()
                ensure_social_chat_indexes_schema()
                ensure_user_admin_schema()
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
    ensure_probe_response_branch_schema()
    ensure_chat_log_session_type_schema()
    ensure_chat_log_human_peer_schema()
    ensure_chat_log_memory_extraction_schema()
    ensure_chat_log_read_schema()
    ensure_chat_log_group_schema()
    ensure_group_owner_schema()
    ensure_group_summary_branch_schema()
    ensure_social_chat_indexes_schema()
    ensure_user_admin_schema()
    ensure_postgres_extensions()
    ensure_postgres_rag_schema()
    ensure_postgres_event_log_triggers()
