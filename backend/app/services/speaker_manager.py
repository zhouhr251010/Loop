"""Speaker turn management for Agent-only group rooms."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import models
from app.crud.chat import create_group_message_log
from app.database import IS_POSTGRES, SessionLocal
from app.services.branching import (
    branch_exists,
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.core_memory_service import format_core_memory_for_prompt
from app.services.llm_service import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_CHAT_REASONING_EFFORT,
    DEFAULT_CHAT_THINKING_MODE,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    _chat_completion_content,
    _create_deepseek_chat_completion,
    build_async_deepseek_client,
)
from app.services.rolling_summary import (
    build_group_context,
    collect_group_messages_for_summary,
    update_group_summary_background,
)
from app.services.time_machine import TimeMachine


logger = logging.getLogger(__name__)
_group_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _timeout_class_names(exc: Exception) -> set[str]:
    return {
        exc.__class__.__name__.lower(),
        *(base.__name__.lower() for base in exc.__class__.__mro__),
    }


def is_timeout_exception(exc: Exception) -> bool:
    """Classify common async LLM timeout failures."""
    message = str(exc).lower()
    return any("timeout" in name for name in _timeout_class_names(exc)) or "timed out" in message


def _agent_member_ids(db: Session, group_id: str) -> list[int]:
    rows = (
        db.query(models.GroupMember.entity_id)
        .filter(
            models.GroupMember.group_id == group_id,
            models.GroupMember.entity_type == models.GroupEntityType.AGENT.value,
        )
        .order_by(models.GroupMember.id.asc())
        .all()
    )
    agent_ids: list[int] = []
    for row in rows:
        try:
            agent_ids.append(int(row[0]))
        except (TypeError, ValueError):
            continue
    return agent_ids


def _last_spoken_at(
    db: Session,
    *,
    group_id: str,
    agent_id: int,
    branch_id: str,
) -> datetime | None:
    read_windows = get_branch_read_windows(db, branch_id)
    row = (
        db.query(models.ChatLog.timestamp)
        .filter(
            models.ChatLog.group_id == group_id,
            models.ChatLog.agent_id == agent_id,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
            models.ChatLog.session_type == models.SessionType.GROUP_SHARED.value,
            models.ChatLog.sender_user_id.is_(None),
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .first()
    )
    return row[0] if row else None


def _choose_current_speaker(
    db: Session,
    group_id: str,
    agent_ids: list[int],
    branch_id: str,
) -> int:
    """Choose the Agent that has waited longest since its last group turn."""
    never_spoken = datetime.min
    ranked = [
        (
            _last_spoken_at(
                db,
                group_id=group_id,
                agent_id=agent_id,
                branch_id=branch_id,
            )
            or never_spoken,
            agent_id,
        )
        for agent_id in agent_ids
    ]
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][1]


def _fallback_agent_group_reply(agent: models.Agent, group_context: str) -> str:
    return (
        f"我是 {agent.agent_name}。结合当前群聊上下文，我先补充一个谨慎的观点："
        "我们可以把刚才的讨论拆成事实、分歧和下一步行动，避免大家同时往不同方向发散。"
    )


async def _generate_agent_group_reply(
    agent: models.Agent,
    group_context: str,
    core_memory_prompt: str,
) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return _fallback_agent_group_reply(agent, group_context)

    prompt = (
        "你正在一个 Agent-only 群聊房间中发言。"
        "请只代表你自己的身份和视角，基于群聊前情提要与最近原文消息自然回复。"
        "不要一次性替其他 Agent 发言，不要宣布系统状态，不要修改记忆。\n\n"
        f"你的 Agent 名称: {agent.agent_name}\n"
        f"{core_memory_prompt}\n\n"
        f"【群聊上下文】\n{group_context}\n\n"
        "现在轮到你抢到麦克风。请输出一条简洁但有推进作用的群聊消息。"
    )
    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    try:
        response = await _create_deepseek_chat_completion(
            async_client=async_client,
            model_name=DEFAULT_CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You generate exactly one Agent group-chat message.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            thinking_mode=DEFAULT_CHAT_THINKING_MODE,
            reasoning_effort=DEFAULT_CHAT_REASONING_EFFORT,
        )
        reply = _chat_completion_content(response).strip()
        return reply[:1800] or _fallback_agent_group_reply(agent, group_context)
    finally:
        await async_client.close()


def _try_acquire_db_lock(db: Session, group_id: str) -> bool:
    if not IS_POSTGRES:
        return True
    result = db.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:lock_key))"),
        {"lock_key": f"loop:agent_group_turn:{group_id}"},
    ).scalar()
    return bool(result)


def _release_db_lock(db: Session, group_id: str) -> None:
    if not IS_POSTGRES:
        return
    try:
        db.execute(
            text("SELECT pg_advisory_unlock(hashtext(:lock_key))"),
            {"lock_key": f"loop:agent_group_turn:{group_id}"},
        )
    except Exception:
        logger.warning(
            "[Speaker Manager] failed to release advisory lock group_id=%s",
            group_id,
            exc_info=True,
        )


def _core_memory_prompt_for_branch(
    db: Session,
    agent: models.Agent,
    branch_id: str,
) -> str:
    normalized_branch_id = normalize_branch_id(branch_id)
    if normalized_branch_id == "main":
        return format_core_memory_for_prompt(getattr(agent.user, "core_memory", None))
    state = TimeMachine(db).reconstruct_state(
        agent_id=agent.id,
        target_timestamp=models.utc_now_seconds(),
        branch_id=normalized_branch_id,
    )
    return format_core_memory_for_prompt(state.get("core_memory"))


def _schedule_summary_update(group_id: str, branch_id: str, db: Session) -> None:
    ejected_messages = collect_group_messages_for_summary(group_id, db, branch_id)
    if not ejected_messages:
        return
    asyncio.create_task(
        update_group_summary_background(
            group_id,
            ejected_messages,
            SessionLocal,
            branch_id,
        ),
    )


async def trigger_agent_group_turn(
    group_id: str,
    branch_id: str,
    db: Session,
) -> dict[str, Any]:
    """Run exactly one Agent turn for an AGENT_ONLY group."""
    normalized_group_id = str(group_id or "").strip()
    normalized_branch_id = normalize_branch_id(branch_id)
    if not normalized_group_id:
        raise ValueError("group_id must not be blank.")
    if not branch_exists(db, normalized_branch_id):
        raise ValueError("Branch not found.")

    local_lock = _group_locks[normalized_group_id]
    if local_lock.locked():
        return {"status": "busy", "group_id": normalized_group_id}

    async with local_lock:
        if not _try_acquire_db_lock(db, normalized_group_id):
            return {"status": "busy", "group_id": normalized_group_id}
        try:
            group = (
                db.query(models.Group)
                .filter(models.Group.id == normalized_group_id)
                .first()
            )
            if group is None:
                raise ValueError("Group not found.")
            if group.group_type != models.GroupType.AGENT_ONLY.value:
                raise ValueError("Agent group turn requires an AGENT_ONLY group.")

            agent_ids = _agent_member_ids(db, normalized_group_id)
            if not agent_ids:
                return {
                    "status": "empty",
                    "group_id": normalized_group_id,
                    "agent_id": None,
                    "content": "",
                }

            current_speaker_id = _choose_current_speaker(
                db,
                normalized_group_id,
                agent_ids,
                normalized_branch_id,
            )
            agent = db.get(models.Agent, current_speaker_id)
            if agent is None:
                raise ValueError("Current speaker Agent not found.")

            group_context = build_group_context(
                normalized_group_id,
                db,
                normalized_branch_id,
            )
            content = await _generate_agent_group_reply(
                agent,
                group_context,
                _core_memory_prompt_for_branch(db, agent, normalized_branch_id),
            )
            chat_log = create_group_message_log(
                db,
                anchor_agent_id=agent.id,
                speaker_agent_id=agent.id,
                content=content,
                group_id=normalized_group_id,
                branch_id=normalized_branch_id,
                session_id=f"group:{normalized_group_id}",
                topic=group.topic or "agent_group",
            )
            _schedule_summary_update(normalized_group_id, normalized_branch_id, db)
            return {
                "status": "ok",
                "group_id": normalized_group_id,
                "branch_id": normalized_branch_id,
                "agent_id": agent.id,
                "agent_name": agent.agent_name,
                "content": content,
                "chat_log_id": chat_log.id,
                "timestamp": chat_log.timestamp,
            }
        finally:
            _release_db_lock(db, normalized_group_id)
