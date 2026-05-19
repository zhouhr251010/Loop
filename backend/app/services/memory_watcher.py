"""Background memory extraction for private chat turns."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.services.branching import normalize_branch_id
from app.services.core_memory_service import merge_core_memory_insight
from app.services.llm_service import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_CHAT_REASONING_EFFORT,
    DEFAULT_CHAT_THINKING_MODE,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    build_async_deepseek_client,
    _chat_completion_content,
    _create_deepseek_chat_completion,
)
from app.services.time_machine import TimeMachine


logger = logging.getLogger(__name__)
MEMORY_WATCHER_MAX_INPUT_CHARS = 3000
MEMORY_WATCHER_MAX_FACT_CHARS = 1000
H2H_SLIDING_WINDOW_SIZE = 20
H2H_WATCHER_MAX_MESSAGE_CHARS = 600
H2H_WATCHER_MAX_INPUT_CHARS = 8000
H2H_WATCHER_MAX_INSIGHT_CHARS = 1600


def _branch_core_memory_base(
    db: Session,
    agent_id: int,
    branch_id: str,
) -> dict[str, str] | None:
    """Return a branch-local core-memory base for non-main writes."""
    normalized_branch_id = normalize_branch_id(branch_id)
    if normalized_branch_id == "main":
        return None
    state = TimeMachine(db).reconstruct_state(
        agent_id=agent_id,
        target_timestamp=models.utc_now_seconds(),
        branch_id=normalized_branch_id,
    )
    core_memory = state.get("core_memory")
    return core_memory if isinstance(core_memory, dict) else None


MEMORY_WATCHER_SYSTEM_PROMPT = (
    "你是一个冷酷的信息抽取机器，不需要和用户对话。"
    "阅读以下用户和 Agent 的最新对话。"
    "如果用户提到了新的核心身份信息（如学历变迁、城市搬迁、职业规划、"
    "重大人生决定、稳定偏好），请将其提炼为简洁的事实描述；"
    "如果只是普通闲聊，请严格返回 'NONE'。"
)

H2H_MEMORY_WATCHER_SYSTEM_PROMPT = (
    "你是 Loop 2.0 的旁路监听者，不参与对话，只做批处理提炼。"
    "请阅读用户与其他真人的聊天片段，分析该用户在真实社交中展现出的"
    "性格特征、沟通语气和当前关注点。只提取稳定或阶段性明显的信息，"
    "不要臆测隐私，不要逐条复述。如果提供的文本只是日常寒暄、无意义废话，"
    "没有体现出明确的性格特征或人际关系变化，必须返回 "
    "{\"has_valuable_insight\": false}。有价值时返回紧凑 JSON，字段为 "
    "has_valuable_insight、persona_traits、communication_style、"
    "current_focus、confidence。"
)

GROUP_MEMORY_WATCHER_SYSTEM_PROMPT = (
    "你是 Loop 2.0 的群聊影子监听者，不参与对话，只做安全的单向表达风格提炼。"
    "这只是该用户在多人社交群组里的单向发言摘要，请忽略任何缺失的上下文，"
    "不要受到群里其他潜在成员的干扰。请纯粹分析该用户本人展现出的表达习惯、"
    "社交姿态和当前核心关注点，并提炼为精简的 JSON。"
    "不要臆测隐私，不要把其他人的观点写到该用户身上，不要逐条复述消息。"
    "如果提供的文本只是日常寒暄、无意义废话，没有体现出明确的性格特征或"
    "人际关系变化，必须返回 {\"has_valuable_insight\": false}。"
    "有价值时返回字段为 has_valuable_insight、expression_habits、"
    "social_posture、current_focus、confidence。"
)


def _bounded_text(value: object, limit: int = MEMORY_WATCHER_MAX_INPUT_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _clean_extracted_fact(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = text.strip("`'\"“”‘’ \n\r\t")
    if text.rstrip(".。!！").upper() == "NONE":
        return ""
    if text.startswith("```"):
        text = text.strip("`").strip()
    if text.rstrip(".。!！").upper() == "NONE":
        return ""
    return text[:MEMORY_WATCHER_MAX_FACT_CHARS].strip()


def _extract_json_object(value: str) -> dict[str, object]:
    text = (value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _string_field(value: object, limit: int = 500) -> str:
    if isinstance(value, (list, tuple)):
        text = "；".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value or "").strip()
    return text[:limit].strip()


def _has_valuable_insight(value: dict[str, object]) -> bool:
    raw_value = value.get("has_valuable_insight")
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"true", "yes", "1", "有", "是"}
    return False


def _h2h_insight_from_json(value: dict[str, object]) -> str:
    raw_insight = _string_field(value.get("insight"), H2H_WATCHER_MAX_INSIGHT_CHARS)
    if raw_insight.rstrip(".。!！").upper() == "NONE":
        return ""

    persona_traits = _string_field(value.get("persona_traits"))
    communication_style = _string_field(value.get("communication_style"))
    current_focus = _string_field(value.get("current_focus"))
    confidence = _string_field(value.get("confidence"), 80)
    if not any((persona_traits, communication_style, current_focus, raw_insight)):
        return ""

    parts = ["H2H 旁路监听批处理洞察："]
    if persona_traits:
        parts.append(f"性格/身份特征：{persona_traits}")
    if communication_style:
        parts.append(f"沟通风格：{communication_style}")
    if current_focus:
        parts.append(f"当前关注点：{current_focus}")
    if raw_insight and raw_insight not in "；".join(parts):
        parts.append(f"补充摘要：{raw_insight}")
    if confidence:
        parts.append(f"置信度：{confidence}")
    return "；".join(parts)[:H2H_WATCHER_MAX_INSIGHT_CHARS].strip()


def _group_insight_from_json(value: dict[str, object]) -> str:
    raw_insight = _string_field(value.get("insight"), H2H_WATCHER_MAX_INSIGHT_CHARS)
    if raw_insight.rstrip(".。!！").upper() == "NONE":
        return ""

    expression_habits = _string_field(value.get("expression_habits"))
    social_posture = _string_field(value.get("social_posture"))
    current_focus = _string_field(value.get("current_focus"))
    confidence = _string_field(value.get("confidence"), 80)
    if not any((expression_habits, social_posture, current_focus, raw_insight)):
        return ""

    parts = ["群聊单向表达风格洞察："]
    if expression_habits:
        parts.append(f"表达习惯：{expression_habits}")
    if social_posture:
        parts.append(f"社交姿态：{social_posture}")
    if current_focus:
        parts.append(f"当前关注点：{current_focus}")
    if raw_insight and raw_insight not in "；".join(parts):
        parts.append(f"补充摘要：{raw_insight}")
    if confidence:
        parts.append(f"置信度：{confidence}")
    return "；".join(parts)[:H2H_WATCHER_MAX_INSIGHT_CHARS].strip()


def _format_h2h_batch_for_prompt(chat_texts: list[str]) -> str:
    lines: list[str] = []
    for index, text in enumerate(chat_texts[:H2H_SLIDING_WINDOW_SIZE], start=1):
        clean_text = _bounded_text(text, H2H_WATCHER_MAX_MESSAGE_CHARS)
        if clean_text:
            lines.append(f"{index}. {clean_text}")
    return "\n".join(lines)[:H2H_WATCHER_MAX_INPUT_CHARS]


async def _extract_memory_fact(user_message: str, agent_reply: str) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        logger.info("[Memory Watcher] DEEPSEEK_API_KEY is not configured; skipped.")
        return ""

    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    try:
        response = await _create_deepseek_chat_completion(
            async_client=async_client,
            model_name=DEFAULT_CHAT_MODEL,
            messages=[
                {"role": "system", "content": MEMORY_WATCHER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "最新对话：\n"
                        f"User: {_bounded_text(user_message)}\n"
                        f"Agent: {_bounded_text(agent_reply)}\n\n"
                        "只输出简洁事实描述或 NONE。"
                    ),
                },
            ],
            max_tokens=240,
            thinking_mode=DEFAULT_CHAT_THINKING_MODE,
            reasoning_effort=DEFAULT_CHAT_REASONING_EFFORT,
        )
        return _clean_extracted_fact(_chat_completion_content(response))
    finally:
        await async_client.close()


async def _extract_h2h_memory_insight(chat_texts: list[str]) -> tuple[bool, str]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        logger.info("[H2H Memory Watcher] DEEPSEEK_API_KEY is not configured; skipped.")
        return False, ""

    batch_text = _format_h2h_batch_for_prompt(chat_texts)
    if not batch_text:
        return False, ""

    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    try:
        response = await _create_deepseek_chat_completion(
            async_client=async_client,
            model_name=DEFAULT_CHAT_MODEL,
            messages=[
                {"role": "system", "content": H2H_MEMORY_WATCHER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "以下是同一用户最近一批真人点对点聊天中由该用户发出的消息：\n"
                        f"{batch_text}\n\n"
                        "请只输出 JSON，不要 Markdown。"
                    ),
                },
            ],
            max_tokens=500,
            thinking_mode=DEFAULT_CHAT_THINKING_MODE,
            reasoning_effort=DEFAULT_CHAT_REASONING_EFFORT,
        )
        raw_content = _chat_completion_content(response)
        parsed_content = _extract_json_object(raw_content)
        if not _has_valuable_insight(parsed_content):
            return False, ""
        insight = _h2h_insight_from_json(parsed_content)
        logger.info("[H2H Memory Watcher] extracted insight=%r", insight)
        return True, insight
    finally:
        await async_client.close()


async def _extract_group_memory_insight(chat_texts: list[str]) -> tuple[bool, str]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        logger.info("[Group Memory Watcher] DEEPSEEK_API_KEY is not configured; skipped.")
        return False, ""

    batch_text = _format_h2h_batch_for_prompt(chat_texts)
    if not batch_text:
        return False, ""

    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    try:
        response = await _create_deepseek_chat_completion(
            async_client=async_client,
            model_name=DEFAULT_CHAT_MODEL,
            messages=[
                {"role": "system", "content": GROUP_MEMORY_WATCHER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "以下是同一用户最近一批真人群聊中由该用户本人发出的消息。"
                        "这些片段没有完整上下文，只能用于单向分析该用户自己的表达：\n"
                        f"{batch_text}\n\n"
                        "请只输出 JSON，不要 Markdown。"
                    ),
                },
            ],
            max_tokens=500,
            thinking_mode=DEFAULT_CHAT_THINKING_MODE,
            reasoning_effort=DEFAULT_CHAT_REASONING_EFFORT,
        )
        raw_content = _chat_completion_content(response)
        parsed_content = _extract_json_object(raw_content)
        if not _has_valuable_insight(parsed_content):
            return False, ""
        insight = _group_insight_from_json(parsed_content)
        logger.info("[Group Memory Watcher] extracted insight=%r", insight)
        return True, insight
    finally:
        await async_client.close()


def check_and_trigger_h2h_extraction(
    sender_user_id: str,
    db: Session,
    background_tasks: BackgroundTasks,
    branch_id: str = "main",
) -> bool:
    """Claim a full H2H sliding window and enqueue background extraction."""
    try:
        user_id = int(str(sender_user_id).strip())
    except (TypeError, ValueError):
        logger.warning("[H2H Memory Watcher] invalid sender_user_id=%r", sender_user_id)
        return False

    normalized_branch_id = normalize_branch_id(branch_id)
    unprocessed_count = (
        db.query(models.ChatLog.id)
        .filter(
            models.ChatLog.sender_user_id == user_id,
            models.ChatLog.branch_id == normalized_branch_id,
            models.ChatLog.session_type == models.SessionType.HUMAN_TO_HUMAN.value,
            models.ChatLog.is_memory_extracted.is_(False),
        )
        .count()
    )
    if unprocessed_count < H2H_SLIDING_WINDOW_SIZE:
        return False

    rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.sender_user_id == user_id,
            models.ChatLog.branch_id == normalized_branch_id,
            models.ChatLog.session_type == models.SessionType.HUMAN_TO_HUMAN.value,
            models.ChatLog.is_memory_extracted.is_(False),
        )
        .order_by(models.ChatLog.timestamp.asc(), models.ChatLog.id.asc())
        .limit(H2H_SLIDING_WINDOW_SIZE)
        .with_for_update(skip_locked=True)
        .all()
    )
    if len(rows) < H2H_SLIDING_WINDOW_SIZE:
        return False

    agent_id = int(rows[0].agent_id)
    chat_log_ids = [int(row.id) for row in rows]
    chat_texts = [str(row.user_message or "").strip() for row in rows]
    (
        db.query(models.ChatLog)
        .filter(models.ChatLog.id.in_(chat_log_ids))
        .update(
            {models.ChatLog.is_memory_extracted: True},
            synchronize_session=False,
        )
    )
    db.commit()

    background_tasks.add_task(
        extract_h2h_insights_background,
        user_id,
        agent_id,
        normalized_branch_id,
        chat_texts,
        SessionLocal,
    )
    logger.info(
        "[H2H Memory Watcher] claimed %s messages for user_id=%s agent_id=%s",
        len(chat_log_ids),
        user_id,
        agent_id,
    )
    return True


def check_and_trigger_group_extraction(
    sender_user_id: str,
    db: Session,
    background_tasks: BackgroundTasks,
    branch_id: str = "main",
) -> bool:
    """Claim a full human group-chat window and enqueue safe one-way extraction."""
    try:
        user_id = int(str(sender_user_id).strip())
    except (TypeError, ValueError):
        logger.warning("[Group Memory Watcher] invalid sender_user_id=%r", sender_user_id)
        return False

    normalized_branch_id = normalize_branch_id(branch_id)
    unprocessed_count = (
        db.query(models.ChatLog.id)
        .filter(
            models.ChatLog.sender_user_id == user_id,
            models.ChatLog.branch_id == normalized_branch_id,
            models.ChatLog.session_type == models.SessionType.GROUP_SHARED.value,
            models.ChatLog.is_memory_extracted.is_(False),
        )
        .count()
    )
    if unprocessed_count < H2H_SLIDING_WINDOW_SIZE:
        return False

    rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.sender_user_id == user_id,
            models.ChatLog.branch_id == normalized_branch_id,
            models.ChatLog.session_type == models.SessionType.GROUP_SHARED.value,
            models.ChatLog.is_memory_extracted.is_(False),
        )
        .order_by(models.ChatLog.timestamp.asc(), models.ChatLog.id.asc())
        .limit(H2H_SLIDING_WINDOW_SIZE)
        .with_for_update(skip_locked=True)
        .all()
    )
    if len(rows) < H2H_SLIDING_WINDOW_SIZE:
        return False

    agent_id = int(rows[0].agent_id)
    chat_log_ids = [int(row.id) for row in rows]
    chat_texts = [str(row.user_message or "").strip() for row in rows]
    (
        db.query(models.ChatLog)
        .filter(models.ChatLog.id.in_(chat_log_ids))
        .update(
            {models.ChatLog.is_memory_extracted: True},
            synchronize_session=False,
        )
    )
    db.commit()

    background_tasks.add_task(
        extract_group_insights_background,
        user_id,
        agent_id,
        normalized_branch_id,
        chat_texts,
        SessionLocal,
    )
    logger.info(
        "[Group Memory Watcher] claimed %s messages for user_id=%s agent_id=%s",
        len(chat_log_ids),
        user_id,
        agent_id,
    )
    return True


async def extract_h2h_insights_background(
    user_id: int,
    agent_id: int,
    branch_id: str,
    chat_texts: list[str],
    db_session_maker: Callable[[], Session],
) -> None:
    """Extract H2H social insights and merge them into the user's core memory."""
    try:
        has_valuable_insight, insight = await _extract_h2h_memory_insight(chat_texts)
        if not has_valuable_insight:
            logger.info(
                "[H2H Memory Watcher] Skipping core memory update due to low signal "
                "user_id=%s agent_id=%s branch_id=%s",
                user_id,
                agent_id,
                branch_id,
            )
            return
        if not insight:
            logger.info(
                "[H2H Memory Watcher] no durable insight detected user_id=%s",
                user_id,
            )
            return

        normalized_branch_id = normalize_branch_id(branch_id)
        db = db_session_maker()
        try:
            agent = (
                db.query(models.Agent)
                .filter(
                    models.Agent.id == agent_id,
                    models.Agent.user_id == user_id,
                )
                .first()
            )
            if agent is None:
                logger.warning(
                    "[H2H Memory Watcher] skipped mismatched user_id=%s agent_id=%s",
                    user_id,
                    agent_id,
                )
                return

            merge_core_memory_insight(
                db=db,
                user_id=user_id,
                insight=insight,
                agent_id=agent_id,
                branch_id=normalized_branch_id,
                source="h2h_bypass_memory_watcher",
                persist_user_core_memory=normalized_branch_id == "main",
                base_core_memory=_branch_core_memory_base(
                    db,
                    agent_id,
                    normalized_branch_id,
                ),
            )
            logger.info(
                "[H2H Memory Watcher] core memory updated user_id=%s agent_id=%s",
                user_id,
                agent_id,
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "[H2H Memory Watcher] background extraction failed user_id=%s "
            "agent_id=%s: %s",
            user_id,
            agent_id,
            exc,
            exc_info=True,
        )


async def extract_group_insights_background(
    user_id: int,
    agent_id: int,
    branch_id: str,
    chat_texts: list[str],
    db_session_maker: Callable[[], Session],
) -> None:
    """Extract one-way group-chat expression insights into the user's core memory."""
    try:
        has_valuable_insight, insight = await _extract_group_memory_insight(chat_texts)
        if not has_valuable_insight:
            logger.info(
                "[Group Memory Watcher] Skipping core memory update due to low signal "
                "user_id=%s agent_id=%s branch_id=%s",
                user_id,
                agent_id,
                branch_id,
            )
            return
        if not insight:
            logger.info(
                "[Group Memory Watcher] no durable insight detected user_id=%s",
                user_id,
            )
            return

        normalized_branch_id = normalize_branch_id(branch_id)
        db = db_session_maker()
        try:
            agent = (
                db.query(models.Agent)
                .filter(
                    models.Agent.id == agent_id,
                    models.Agent.user_id == user_id,
                )
                .first()
            )
            if agent is None:
                logger.warning(
                    "[Group Memory Watcher] skipped mismatched user_id=%s agent_id=%s",
                    user_id,
                    agent_id,
                )
                return

            merge_core_memory_insight(
                db=db,
                user_id=user_id,
                insight=insight,
                agent_id=agent_id,
                branch_id=normalized_branch_id,
                source="group_bypass_memory_watcher",
                persist_user_core_memory=normalized_branch_id == "main",
                base_core_memory=_branch_core_memory_base(
                    db,
                    agent_id,
                    normalized_branch_id,
                ),
            )
            logger.info(
                "[Group Memory Watcher] core memory updated user_id=%s agent_id=%s",
                user_id,
                agent_id,
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "[Group Memory Watcher] background extraction failed user_id=%s "
            "agent_id=%s: %s",
            user_id,
            agent_id,
            exc,
            exc_info=True,
        )


async def extract_and_update_memory_background(
    user_id: int,
    agent_id: int,
    branch_id: str,
    session_id: str,
    user_message: str,
    agent_reply: str,
) -> None:
    """Extract durable identity facts after chat storage without affecting API status."""
    
    # 【补丁 1：确认 FastAPI 确实拉起了这个后台任务】
    logger.info(
        f"[Memory Watcher] Started for agent_id={agent_id}, branch_id={branch_id}, session_id={session_id}"
    )
    
    try:
        fact = await _extract_memory_fact(user_message, agent_reply)
        
        # 【补丁 2：无论 LLM 输出了什么（就算它偷懒了），先打印出来看看】
        logger.info(f"[Memory Watcher] LLM Output for session_id={session_id}: {fact!r}")
        
        # 【补丁 3：修复拦截逻辑，拦截 None、空字符串，以及大模型返回的 "NONE"】
        if not fact or fact.strip().upper() == "NONE":
            logger.info(f"[Memory Watcher] No new core memory detected. Exiting cleanly.")
            return

        normalized_branch_id = normalize_branch_id(branch_id)
        db = SessionLocal()
        try:
            agent = (
                db.query(models.Agent)
                .filter(
                    models.Agent.id == agent_id,
                    models.Agent.user_id == user_id,
                )
                .first()
            )
            if agent is None:
                logger.warning(
                    "[Memory Watcher] skipped update for mismatched "
                    "user_id=%s agent_id=%s",
                    user_id,
                    agent_id,
                )
                return

            merge_core_memory_insight(
                db=db,
                user_id=user_id,
                insight=fact,
                agent_id=agent_id,
                branch_id=normalized_branch_id,
                source="memory_watcher",
                persist_user_core_memory=normalized_branch_id == "main",
                base_core_memory=_branch_core_memory_base(
                    db,
                    agent_id,
                    normalized_branch_id,
                ),
            )
            logger.info(
                "[Memory Watcher] core memory updated user_id=%s agent_id=%s "
                "branch_id=%s session_id=%s fact=%r",
                user_id,
                agent_id,
                normalized_branch_id,
                session_id,
                fact,
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "[Memory Watcher] background extraction failed user_id=%s "
            "agent_id=%s branch_id=%s session_id=%s: %s",
            user_id,
            agent_id,
            branch_id,
            session_id,
            exc,
            exc_info=True,
        )
