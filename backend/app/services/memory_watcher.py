"""Background memory extraction for private chat turns."""

from __future__ import annotations

import logging
import os

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


logger = logging.getLogger(__name__)
MEMORY_WATCHER_MAX_INPUT_CHARS = 3000
MEMORY_WATCHER_MAX_FACT_CHARS = 1000


MEMORY_WATCHER_SYSTEM_PROMPT = (
    "你是一个冷酷的信息抽取机器，不需要和用户对话。"
    "阅读以下用户和 Agent 的最新对话。"
    "如果用户提到了新的核心身份信息（如学历变迁、城市搬迁、职业规划、"
    "重大人生决定、稳定偏好），请将其提炼为简洁的事实描述；"
    "如果只是普通闲聊，请严格返回 'NONE'。"
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
