"""MemGPT-style core memory helpers for Loop agents."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app import models


CORE_MEMORY_KEYS = ("persona_traits", "key_relationships", "current_goals")
DEFAULT_CORE_MEMORY: dict[str, str] = {
    "persona_traits": "",
    "key_relationships": "",
    "current_goals": "",
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
    return (
        "【最高优先级 Core Memory / 不可滑动核心记忆】\n"
        f"persona_traits: {core_memory['persona_traits'] or '暂无'}\n"
        f"key_relationships: {core_memory['key_relationships'] or '暂无'}\n"
        f"current_goals: {core_memory['current_goals'] or '暂无'}\n"
        "这些内容是你的稳定自我认知，优先级高于 RAG 检索片段和短期上下文。"
    )


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
    user.core_memory = core_memory
    db.commit()
    db.refresh(user)
    return core_memory


def merge_core_memory_insight(
    db: Session,
    user_id: int,
    insight: str,
) -> dict[str, str]:
    """Append a high-level reflection into persona core memory."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise ValueError("User not found.")

    core_memory = normalize_core_memory(user.core_memory)
    clean_insight = (insight or "").strip()
    if not clean_insight:
        return core_memory

    existing = core_memory["persona_traits"].strip()
    if existing:
        core_memory["persona_traits"] = f"{existing}\n- {clean_insight}"[-8000:]
    else:
        core_memory["persona_traits"] = f"- {clean_insight}"[-8000:]

    user.core_memory = core_memory
    db.commit()
    db.refresh(user)
    return core_memory
