"""Feedback reflection and in-context memory evolution helpers."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.services.branching import normalize_branch_id
from app.services.core_memory_service import normalize_core_memory
from app.services.event_store import append_event
from app.services.llm_service import build_async_deepseek_client


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_FEEDBACK_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


FEEDBACK_REFLECTION_TIMEOUT_SECONDS = _float_env(
    "LOOP_FEEDBACK_REFLECTION_TIMEOUT_SECONDS",
    12.0,
)
FEEDBACK_REFLECTION_MAX_TOKENS = _int_env(
    "LOOP_FEEDBACK_REFLECTION_MAX_TOKENS",
    160,
)
MAX_FEEDBACK_RULE_CHARS = _int_env("LOOP_FEEDBACK_RULE_CHARS", 240)


def _sanitize_rule(value: str) -> str:
    """Keep the persisted communication-style rule short and single-purpose."""
    clean_value = " ".join((value or "").strip().split())
    clean_value = clean_value.strip("`\"'“”‘’。、， ")
    return clean_value[:MAX_FEEDBACK_RULE_CHARS]


def _fallback_feedback_rule(original_text: str, corrected_text: str) -> str:
    """Return a conservative rule when the remote reflection call is unavailable."""
    original_length = len(original_text.strip())
    corrected_length = len(corrected_text.strip())
    if corrected_length and corrected_length < original_length * 0.7:
        return "用户偏好更克制、简短的表达，倾向删去冗余修饰和不必要的情绪外放"
    if corrected_length > original_length * 1.3:
        return "用户偏好表达更具体、更有个人细节，不喜欢过于笼统的泛泛表述"
    return "用户偏好采用自己修订后的语气和措辞，避免原句中不符合其自我表达习惯的风格"


async def reflect_feedback_rule(original_text: str, corrected_text: str) -> str:
    """Infer one concise communication-style rule from a user's post correction."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return _fallback_feedback_rule(original_text, corrected_text)

    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=FEEDBACK_REFLECTION_TIMEOUT_SECONDS,
    )
    try:
        response = await async_client.chat.completions.create(
            model=DEFAULT_FEEDBACK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 Loop 的反馈反思引擎。你只总结用户纠错体现出的"
                        "稳定表达偏好或性格化表达规则，不评价内容真假。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "对比原帖和用户修改后的帖子，输出一条极其精炼的中文规则。"
                        "要求：只输出一句话；不超过 60 个汉字；不要解释；不要编号；"
                        "格式类似“用户偏好……，不喜欢……”。\n\n"
                        f"原帖：{original_text}\n"
                        f"用户修改后：{corrected_text}"
                    ),
                },
            ],
            max_tokens=FEEDBACK_REFLECTION_MAX_TOKENS,
            temperature=0.2,
        )
        rule = _sanitize_rule(response.choices[0].message.content or "")
        return rule or _fallback_feedback_rule(original_text, corrected_text)
    except Exception:
        return _fallback_feedback_rule(original_text, corrected_text)
    finally:
        await async_client.close()


def append_feedback_rule_to_core_memory(
    db: Session,
    *,
    user_id: int,
    agent_id: int,
    feedback_log_id: int,
    post_id: int,
    branch_id: str,
    original_text: str,
    corrected_text: str,
    rule: str,
) -> dict[str, str]:
    """Append a feedback-derived communication rule without overwriting memory."""
    normalized_branch_id = normalize_branch_id(branch_id)
    clean_rule = _sanitize_rule(rule)
    if not clean_rule:
        clean_rule = _fallback_feedback_rule(original_text, corrected_text)

    user = (
        db.query(models.User)
        .filter(models.User.id == user_id)
        .with_for_update()
        .first()
    )
    if user is None:
        raise ValueError("User not found.")

    core_memory = normalize_core_memory(user.core_memory)
    existing_style = core_memory.get("communication_style", "").strip()
    new_rule_line = f"- {clean_rule}"
    if clean_rule in existing_style:
        updated_style = existing_style
    elif existing_style:
        updated_style = f"{existing_style}\n{new_rule_line}"
    else:
        updated_style = new_rule_line

    core_memory = {
        **core_memory,
        "communication_style": updated_style,
    }
    timestamp = utc_now_seconds()
    user.core_memory = core_memory
    append_event(
        db,
        agent_id=agent_id,
        branch_id=normalized_branch_id,
        event_type="CORE_MEMORY_UPDATED",
        payload={
            "source": "feedback_reflection",
            "reason": "user_corrected_agent_post",
            "user_id": user_id,
            "feedback_log_id": feedback_log_id,
            "post_id": post_id,
            "branch_id": normalized_branch_id,
            "key": "communication_style",
            "appended_rule": clean_rule,
            "original_text": original_text,
            "corrected_text": corrected_text,
            "core_memory": core_memory,
        },
        timestamp=timestamp,
        commit=False,
    )
    db.commit()
    db.refresh(user)
    return core_memory


async def reflect_and_merge_feedback(
    db: Session,
    *,
    feedback_log: models.FeedbackLog,
    post: models.Post,
    branch_id: str,
) -> str:
    """Turn a stored correction into durable in-context communication memory."""
    rule = await reflect_feedback_rule(
        feedback_log.original_text,
        feedback_log.corrected_text,
    )
    append_feedback_rule_to_core_memory(
        db,
        user_id=feedback_log.user_id,
        agent_id=post.agent_id,
        feedback_log_id=feedback_log.id,
        post_id=post.id,
        branch_id=branch_id,
        original_text=feedback_log.original_text,
        corrected_text=feedback_log.corrected_text,
        rule=rule,
    )
    return rule
