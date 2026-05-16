"""Startup seeding for system-level non-participant NPC agents."""

import hashlib
import re
from uuid import uuid4

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app import models
from app.crud.user import hash_password
from app.database import IS_POSTGRES, SessionLocal
from app.services.core_memory_service import DEFAULT_CORE_MEMORY
from app.services.event_store import append_event


SYSTEM_NPC_USERNAME = "__loop_system_npc__"
SYSTEM_NPC_AGENT_NAME = "System_NPC"
SYSTEM_NPC_SEED_LOCK_ID = 817504202
SENDER_NPC_USERNAME_PREFIX = "__loop_npc_"
MAX_AGENT_NAME_LENGTH = 128


def _sender_hash(sender_id: str) -> str:
    return hashlib.sha1(sender_id.encode("utf-8")).hexdigest()[:16]


def _sender_label(sender_id: str) -> str:
    compact = re.sub(r"\s+", "_", sender_id.strip())
    compact = compact.strip("_") or "sender"
    max_sender_chars = MAX_AGENT_NAME_LENGTH - len("NPC_")
    return f"NPC_{compact[:max_sender_chars]}"


def _sender_username(sender_id: str) -> str:
    return f"{SENDER_NPC_USERNAME_PREFIX}{_sender_hash(sender_id)}__"


def ensure_npc_agent_for_sender(
    db: Session,
    sender_id: str,
) -> tuple[models.User, models.Agent]:
    """Create or reuse a stable NPC agent for one external sender id."""
    normalized_sender_id = sender_id.strip()[:128]
    if not normalized_sender_id:
        raise ValueError("sender_id cannot be blank.")

    username = _sender_username(normalized_sender_id)
    system_user = (
        db.query(models.User)
        .filter(func.lower(models.User.username) == username.lower())
        .first()
    )
    if system_user is None:
        system_user = models.User(
            username=username,
            password_hash=hash_password(str(uuid4())),
            autobiography=(
                "System-owned NPC account for imported group-chat sender "
                f"{normalized_sender_id!r}."
            ),
            core_memory=DEFAULT_CORE_MEMORY.copy(),
        )
        db.add(system_user)
        db.flush()

    db_agent = (
        db.query(models.Agent)
        .filter(models.Agent.user_id == system_user.id)
        .first()
    )
    if db_agent is not None:
        if not db_agent.is_npc:
            db_agent.is_npc = True
            db.flush()
        return system_user, db_agent

    agent_name = _sender_label(normalized_sender_id)
    system_prompt_base = (
        "You are a system NPC created from an imported group-chat sender. "
        f"External sender_id: {normalized_sender_id}. Do not represent a real "
        "Loop participant or core experiment identity."
    )
    db_agent = models.Agent(
        user_id=system_user.id,
        agent_name=agent_name,
        system_prompt_base=system_prompt_base,
        is_npc=True,
    )
    db.add(db_agent)
    db.flush()
    append_event(
        db,
        agent_id=db_agent.id,
        event_type="AGENT_CREATED",
        payload={
            "user_id": db_agent.user_id,
            "agent_name": db_agent.agent_name,
            "system_prompt_base": db_agent.system_prompt_base,
            "is_npc": db_agent.is_npc,
            "source_sender_id": normalized_sender_id,
        },
        commit=False,
    )
    return system_user, db_agent


def ensure_system_npc_agent() -> None:
    """Create one default NPC agent for group-chat and manual-event mapping."""
    db = SessionLocal()
    try:
        if IS_POSTGRES:
            db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": SYSTEM_NPC_SEED_LOCK_ID},
            )
        existing_npc = (
            db.query(models.Agent)
            .filter(models.Agent.is_npc.is_(True))
            .order_by(models.Agent.id.asc())
            .first()
        )
        if existing_npc is not None:
            db.commit()
            return

        system_user = (
            db.query(models.User)
            .filter(func.lower(models.User.username) == SYSTEM_NPC_USERNAME.lower())
            .first()
        )
        if system_user is None:
            system_user = models.User(
                username=SYSTEM_NPC_USERNAME,
                password_hash=hash_password(str(uuid4())),
                autobiography=(
                    "System-owned NPC account for non-participant group-chat "
                    "speakers and manual research events."
                ),
                core_memory=DEFAULT_CORE_MEMORY.copy(),
            )
            db.add(system_user)
            db.flush()

        system_prompt_base = (
            "You are a system NPC used only as a non-participant placeholder "
            "in Loop research workflows. Do not represent a real participant "
            "or core experiment identity."
        )
        db_agent = models.Agent(
            user_id=system_user.id,
            agent_name=SYSTEM_NPC_AGENT_NAME,
            system_prompt_base=system_prompt_base,
            is_npc=True,
        )
        db.add(db_agent)
        db.flush()
        append_event(
            db,
            agent_id=db_agent.id,
            event_type="AGENT_CREATED",
            payload={
                "user_id": db_agent.user_id,
                "agent_name": db_agent.agent_name,
                "system_prompt_base": db_agent.system_prompt_base,
                "is_npc": db_agent.is_npc,
            },
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
