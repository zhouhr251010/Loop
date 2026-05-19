"""Rolling summary guardrail for N-to-N chat room context."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.services.branching import (
    BranchReadWindow,
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.llm_service import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_CHAT_REASONING_EFFORT,
    DEFAULT_CHAT_THINKING_MODE,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    _chat_completion_content,
    _create_deepseek_chat_completion,
    build_async_deepseek_client,
)


logger = logging.getLogger(__name__)

MAX_RAW_MESSAGES = 15
MAX_MESSAGE_CHARS = 900
MAX_SUMMARY_INPUT_CHARS = 9000
MAX_SUMMARY_OUTPUT_CHARS = 5000

ROLLING_SUMMARY_SYSTEM_PROMPT = (
    "你是 Loop 2.0 群聊上下文压缩器。你的任务是把旧的群聊摘要和刚被挤出"
    "原文窗口的消息压缩成新的前情提要。保留长期议题、关键分歧、已达成共识、"
    "未解决问题和对后续发言有用的人物立场。不要逐条复述，不要加入新事实。"
)


def _clean_text(value: object, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _message_sender(row: models.ChatLog) -> str:
    if row.sender_user_id is not None:
        return f"User #{row.sender_user_id}"
    if row.agent_id is not None:
        return f"Agent #{row.agent_id}"
    return "Unknown"


def _format_chat_log(row: models.ChatLog) -> str:
    content = _clean_text(row.user_message or row.agent_reply)
    if not content:
        content = "[empty message]"
    timestamp = row.timestamp.isoformat() if row.timestamp else ""
    prefix = f"{timestamp} " if timestamp else ""
    return f"{prefix}{_message_sender(row)}: {content}"


def _coerce_message_text(message: Any) -> str:
    if isinstance(message, models.ChatLog):
        return _format_chat_log(message)
    if isinstance(message, dict):
        content = (
            message.get("content")
            or message.get("user_message")
            or message.get("agent_reply")
            or ""
        )
        sender = (
            message.get("sender")
            or message.get("sender_user_id")
            or message.get("agent_id")
            or "Unknown"
        )
        timestamp = str(message.get("timestamp") or "").strip()
        prefix = f"{timestamp} " if timestamp else ""
        return f"{prefix}{sender}: {_clean_text(content)}"
    return _clean_text(message)


def _format_message_batch(messages: Sequence[Any]) -> str:
    lines: list[str] = []
    for index, message in enumerate(messages, start=1):
        text = _coerce_message_text(message)
        if text:
            lines.append(f"{index}. {text}")
    return "\n".join(lines)[:MAX_SUMMARY_INPUT_CHARS]


def _serialize_chat_log_for_summary(row: models.ChatLog) -> dict[str, object]:
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat() if row.timestamp else "",
        "sender_user_id": row.sender_user_id,
        "agent_id": row.agent_id,
        "content": row.user_message or row.agent_reply,
    }


def _fallback_summary(old_summary: str, message_text: str) -> str:
    combined = (
        f"{old_summary.strip()}\n"
        "【新压实消息要点】\n"
        f"{message_text.strip()}"
    ).strip()
    return combined[-MAX_SUMMARY_OUTPUT_CHARS:]


def _get_exact_group_summary(
    db: Session,
    group_id: str,
    branch_id: str,
) -> models.GroupSummary | None:
    return (
        db.query(models.GroupSummary)
        .filter(
            models.GroupSummary.group_id == group_id,
            models.GroupSummary.branch_id == branch_id,
        )
        .first()
    )


def _summary_visible_in_window(
    db: Session,
    summary: models.GroupSummary,
    window: BranchReadWindow,
) -> bool:
    """Return whether a branch summary only covers messages visible in this window."""
    last_message_id = str(summary.last_summarized_message_id or "").strip()
    if not last_message_id:
        return window.until_timestamp is None
    try:
        chat_log_id = int(last_message_id)
    except ValueError:
        return False

    row = db.get(models.ChatLog, chat_log_id)
    if row is None:
        return False
    if row.group_id != summary.group_id:
        return False
    if normalize_branch_id(row.branch_id) != window.branch_id:
        return False
    if window.until_timestamp is None:
        return True
    return row.timestamp <= window.until_timestamp


def _get_visible_group_summary(
    db: Session,
    group_id: str,
    branch_id: str,
) -> models.GroupSummary | None:
    """Return exact branch summary, or nearest visible ancestor summary."""
    normalized_branch_id = normalize_branch_id(branch_id)
    exact_summary = _get_exact_group_summary(db, group_id, normalized_branch_id)
    if exact_summary is not None:
        return exact_summary

    windows = get_branch_read_windows(db, normalized_branch_id)
    for window in reversed(windows[:-1]):
        summary = _get_exact_group_summary(db, group_id, window.branch_id)
        if summary is None or not summary.summary_text.strip():
            continue
        if _summary_visible_in_window(db, summary, window):
            return summary
    return None


async def _summarize_messages(old_summary: str, message_text: str) -> str:
    if not message_text.strip():
        return old_summary.strip()

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        logger.info("[Rolling Summary] DEEPSEEK_API_KEY is not configured; using fallback.")
        return _fallback_summary(old_summary, message_text)

    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=DEFAULT_LLM_TIMEOUT_SECONDS,
    )
    try:
        response = await _create_deepseek_chat_completion(
            async_client=async_client,
            model_name=DEFAULT_CHAT_MODEL,
            messages=[
                {"role": "system", "content": ROLLING_SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "旧群聊摘要：\n"
                        f"{old_summary.strip() or '暂无'}\n\n"
                        "需要压实进摘要的旧消息：\n"
                        f"{message_text}\n\n"
                        "请输出更新后的群聊前情提要，控制在 800 字以内。"
                    ),
                },
            ],
            max_tokens=900,
            thinking_mode=DEFAULT_CHAT_THINKING_MODE,
            reasoning_effort=DEFAULT_CHAT_REASONING_EFFORT,
        )
        summary = _chat_completion_content(response).strip()
        return (summary or _fallback_summary(old_summary, message_text))[
            -MAX_SUMMARY_OUTPUT_CHARS:
        ]
    finally:
        await async_client.close()


def build_group_context(group_id: str, db: Session, branch_id: str = "main") -> str:
    """Return rolling-summary context plus the latest bounded raw messages."""
    normalized_group_id = str(group_id or "").strip()
    normalized_branch_id = normalize_branch_id(branch_id)
    if not normalized_group_id:
        raise ValueError("group_id must not be blank.")

    group = db.query(models.Group).filter(models.Group.id == normalized_group_id).first()
    if group is None:
        raise ValueError("Group not found.")

    summary = _get_visible_group_summary(
        db,
        normalized_group_id,
        normalized_branch_id,
    )
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    recent_rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.group_id == normalized_group_id,
            branch_window_filter(
                models.ChatLog.branch_id,
                models.ChatLog.timestamp,
                None,
                read_windows,
            ),
        )
        .order_by(models.ChatLog.timestamp.desc(), models.ChatLog.id.desc())
        .limit(MAX_RAW_MESSAGES)
        .all()
    )
    recent_rows = list(reversed(recent_rows))

    sections = [
        f"【群组】{group.name}",
        f"【群组类型】{group.group_type}",
    ]
    if group.topic:
        sections.append(f"【主题】{group.topic}")
    if summary is not None and summary.summary_text.strip():
        sections.append(f"【历史前情提要】\n{summary.summary_text.strip()}")
    else:
        sections.append("【历史前情提要】\n暂无")

    raw_message_text = "\n".join(
        _format_chat_log(row)
        for row in recent_rows
        if _format_chat_log(row)
    )
    sections.append(f"【最近 {len(recent_rows)} 条原文消息】\n{raw_message_text or '暂无'}")
    return "\n\n".join(sections)


def collect_group_messages_for_summary(
    group_id: str,
    db: Session,
    branch_id: str = "main",
) -> list[dict[str, object]]:
    """Return group messages that have fallen out of the raw context window."""
    normalized_group_id = str(group_id or "").strip()
    normalized_branch_id = normalize_branch_id(branch_id)
    if not normalized_group_id:
        return []
    summary = _get_exact_group_summary(
        db,
        normalized_group_id,
        normalized_branch_id,
    )
    last_summarized_id = 0
    if summary is not None and summary.last_summarized_message_id:
        try:
            last_summarized_id = int(summary.last_summarized_message_id)
        except ValueError:
            last_summarized_id = 0

    total_unsummarized = (
        db.query(models.ChatLog.id)
        .filter(
            models.ChatLog.group_id == normalized_group_id,
            models.ChatLog.branch_id == normalized_branch_id,
            models.ChatLog.id > last_summarized_id,
        )
        .count()
    )
    ejected_count = max(0, total_unsummarized - MAX_RAW_MESSAGES)
    if ejected_count <= 0:
        return []

    rows = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.group_id == normalized_group_id,
            models.ChatLog.branch_id == normalized_branch_id,
            models.ChatLog.id > last_summarized_id,
        )
        .order_by(models.ChatLog.timestamp.asc(), models.ChatLog.id.asc())
        .limit(ejected_count)
        .all()
    )
    return [_serialize_chat_log_for_summary(row) for row in rows]


async def update_group_summary_background(
    group_id: str,
    new_messages: list[Any],
    db_session_maker: Callable[[], Session],
    branch_id: str = "main",
) -> None:
    """Compress ejected group messages into GroupSummary using an independent DB session."""
    normalized_group_id = str(group_id or "").strip()
    normalized_branch_id = normalize_branch_id(branch_id)
    if not normalized_group_id:
        logger.warning("[Rolling Summary] skipped blank group_id.")
        return
    message_text = _format_message_batch(new_messages)
    if not message_text.strip():
        logger.info("[Rolling Summary] no messages to summarize for group_id=%s", group_id)
        return

    db = db_session_maker()
    try:
        group = db.query(models.Group).filter(models.Group.id == normalized_group_id).first()
        if group is None:
            logger.warning("[Rolling Summary] group not found group_id=%s", group_id)
            return

        summary = _get_exact_group_summary(
            db,
            normalized_group_id,
            normalized_branch_id,
        )
        visible_summary = summary or _get_visible_group_summary(
            db,
            normalized_group_id,
            normalized_branch_id,
        )
        old_summary = visible_summary.summary_text if visible_summary is not None else ""
        new_summary_text = await _summarize_messages(old_summary, message_text)
        last_message = new_messages[-1] if new_messages else None
        last_message_id = ""
        if isinstance(last_message, models.ChatLog):
            last_message_id = str(last_message.id)
        elif isinstance(last_message, dict):
            last_message_id = str(last_message.get("id") or "").strip()

        if summary is None:
            summary = models.GroupSummary(
                group_id=normalized_group_id,
                branch_id=normalized_branch_id,
                summary_text=new_summary_text,
                last_summarized_message_id=last_message_id or None,
            )
            db.add(summary)
        else:
            summary.summary_text = new_summary_text
            if last_message_id:
                summary.last_summarized_message_id = last_message_id
        db.commit()
        logger.info(
            "[Rolling Summary] updated group_id=%s last_message_id=%s",
            normalized_group_id,
            last_message_id or None,
        )
    except Exception as exc:
        db.rollback()
        logger.warning(
            "[Rolling Summary] failed group_id=%s: %s",
            normalized_group_id,
            exc,
            exc_info=True,
        )
    finally:
        db.close()
