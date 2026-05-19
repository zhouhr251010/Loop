"""MemGPT-style core memory helpers for Loop agents."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.services.event_store import append_event


CORE_MEMORY_KEYS = (
    "persona_traits",
    "key_relationships",
    "current_goals",
    "communication_style",
)
PROMPT_CORE_MEMORY_FIELD_LIMIT = 1200
DEFAULT_CORE_MEMORY: dict[str, str] = {
    "persona_traits": "",
    "key_relationships": "",
    "current_goals": "",
    "communication_style": "",
}


def normalize_core_memory(value: Any) -> dict[str, str]:
    """Return a stable core-memory JSON object with required keys."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}

    raw_memory = value if isinstance(value, dict) else {}
    normalized = DEFAULT_CORE_MEMORY.copy()
    for key in CORE_MEMORY_KEYS:
        raw_value = raw_memory.get(key)
        if raw_value is None:
            continue
        if isinstance(raw_value, (dict, list)):
            normalized[key] = json.dumps(raw_value, ensure_ascii=False)
        else:
            normalized[key] = str(raw_value).strip()
    return normalized


def format_core_memory_for_prompt(value: Any) -> str:
    """Render core memory as the highest-priority prompt block."""
    core_memory = normalize_core_memory(value)
    prompt_memory = {
        key: _truncate_prompt_field(core_memory[key])
        for key in CORE_MEMORY_KEYS
    }
    return (
        "【最高优先级 Core Memory / 不可滑动核心记忆】\n"
        f"persona_traits: {prompt_memory['persona_traits'] or '暂无'}\n"
        f"key_relationships: {prompt_memory['key_relationships'] or '暂无'}\n"
        f"current_goals: {prompt_memory['current_goals'] or '暂无'}\n"
        f"communication_style: {prompt_memory['communication_style'] or '暂无'}\n"
        "这些内容是你的稳定自我认知，优先级高于 RAG 检索片段和短期上下文。"
        "请自然体现这些长期身份材料，保持同一个人的连续感。"
    )


def _truncate_prompt_field(value: str) -> str:
    """Keep core memory prompt fields bounded even if persisted memory grows."""
    clean_value = (value or "").strip()
    if len(clean_value) <= PROMPT_CORE_MEMORY_FIELD_LIMIT:
        return clean_value
    return f"{clean_value[:PROMPT_CORE_MEMORY_FIELD_LIMIT]}...[truncated]"


def edit_user_core_memory(
    db: Session,
    user_id: int,
    key: str,
    new_value: str,
) -> dict[str, str]:
    """Update one MemGPT-style core memory field for a user."""
    normalized_key = (key or "").strip()
    if normalized_key not in CORE_MEMORY_KEYS:
        raise ValueError(
            f"core_memory key must be one of: {', '.join(CORE_MEMORY_KEYS)}",
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise ValueError("User not found.")

    core_memory = normalize_core_memory(user.core_memory)
    core_memory[normalized_key] = (new_value or "").strip()[:8000]
    timestamp = utc_now_seconds()
    user.core_memory = core_memory
    if user.agent is not None:
        append_event(
            db,
            agent_id=user.agent.id,
            event_type="CORE_MEMORY_UPDATED",
            payload={
                "key": normalized_key,
                "new_value": core_memory[normalized_key],
                "core_memory": core_memory,
                "source": "edit_core_memory",
            },
            timestamp=timestamp,
            commit=False,
        )
    db.commit()
    db.refresh(user)
    return core_memory


def merge_core_memory_insight(
    db: Session,
    user_id: int,
    insight: str,
    *,
    agent_id: int | None = None,
    branch_id: str = "main",
    source: str = "sleep_consolidation",
    persist_user_core_memory: bool = True,
    base_core_memory: Any | None = None,
) -> dict[str, str]:
    """Append a high-level reflection into persona core memory."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise ValueError("User not found.")

    core_memory = normalize_core_memory(
        base_core_memory if base_core_memory is not None else user.core_memory,
    )
    clean_insight = (insight or "").strip()
    if not clean_insight:
        return core_memory

    existing = core_memory["persona_traits"].strip()
    if clean_insight in existing:
        return core_memory

    if existing:
        core_memory["persona_traits"] = f"{existing}\n- {clean_insight}"[-8000:]
    else:
        core_memory["persona_traits"] = f"- {clean_insight}"[-8000:]

    timestamp = utc_now_seconds()
    if persist_user_core_memory:
        user.core_memory = core_memory
    target_agent_id = agent_id or (user.agent.id if user.agent is not None else None)
    if target_agent_id is not None:
        append_event(
            db,
            agent_id=target_agent_id,
            branch_id=(branch_id or "main").strip() or "main",
            event_type="CORE_MEMORY_UPDATED",
            payload={
                "key": "persona_traits",
                "new_value": core_memory["persona_traits"],
                "insight": clean_insight,
                "core_memory": core_memory,
                "source": source,
            },
            timestamp=timestamp,
            commit=False,
        )
    db.commit()
    db.refresh(user)
    return core_memory
