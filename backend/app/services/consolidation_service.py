"""Offline memory consolidation for Loop agents."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.models import utc_now_seconds
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    branch_exists,
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.core_memory_service import merge_core_memory_insight
from app.services.event_store import append_event
from app.services.llm_service import build_async_deepseek_client
from app.services.rag_service import add_scored_memories
from app.services.time_machine import TimeMachine


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEFAULT_THINKING_MODE = os.getenv("DEEPSEEK_THINKING", "enabled")
DEFAULT_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
DEFAULT_CONSOLIDATION_MODEL = os.getenv("DEEPSEEK_CONSOLIDATION_MODEL", DEFAULT_MODEL)
DEFAULT_CONSOLIDATION_THINKING_MODE = os.getenv(
    "DEEPSEEK_CONSOLIDATION_THINKING",
    "disabled",
)
DEFAULT_CONSOLIDATION_REASONING_EFFORT = os.getenv(
    "DEEPSEEK_CONSOLIDATION_REASONING_EFFORT",
    DEFAULT_REASONING_EFFORT,
)
REFLECTION_BATCH_SIZE = 5
DAILY_CHAT_LOG_LIMIT = 120
DAILY_OWN_POST_LIMIT = 80
DAILY_RECORD_PROMPT_LIMIT = 120
MAX_DAILY_RECORD_CHARS = 1000


class ConsolidationLLMError(RuntimeError):
    """Raised when sleep consolidation cannot safely complete an LLM step."""


def _aborted_consolidation_result(
    *,
    user_id: int,
    agent_id: int,
    records_count: int,
    reason: str,
    branch_id: str = DEFAULT_BRANCH_ID,
) -> dict[str, Any]:
    """Return a safe no-op sleep result after preserving short-term memory."""
    return {
        "message": (
            "Agent sleep consolidation aborted; working memory was preserved "
            f"for retry. Reason: {reason}"
        ),
        "user_id": user_id,
        "agent_id": agent_id,
        "branch_id": branch_id,
        "records_consolidated": records_count,
        "chunks_added": 0,
        "graph_triples_extracted": 0,
        "daily_events_created": 0,
        "high_level_insights_created": 0,
        "core_memory_updated": False,
        "relationship_updates": [],
        "graph_memory_cleared": False,
    }


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(
            "[Sleep Consolidation] Invalid %s=%r; using %.1f.",
            name,
            raw_value,
            default,
        )
        return default
    return max(1.0, value)


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "[Sleep Consolidation] Invalid %s=%r; using %s.",
            name,
            raw_value,
            default,
        )
        return default
    return max(0, value)


CONSOLIDATION_LLM_TIMEOUT_SECONDS = _env_float(
    "LOOP_CONSOLIDATION_LLM_TIMEOUT_SECONDS",
    60.0,
)
CONSOLIDATION_LLM_MAX_RETRIES = _env_int(
    "LOOP_CONSOLIDATION_LLM_MAX_RETRIES",
    0,
)


def _deepseek_extra_body() -> dict[str, dict[str, str]]:
    thinking_mode = DEFAULT_CONSOLIDATION_THINKING_MODE.strip().lower() or "disabled"
    if thinking_mode not in {"enabled", "disabled"}:
        thinking_mode = "disabled"
    if thinking_mode == "disabled":
        return {}
    return {"thinking": {"type": thinking_mode}}


def _deepseek_reasoning_effort() -> str | None:
    extra_body = _deepseek_extra_body()
    if not extra_body or extra_body["thinking"]["type"] != "enabled":
        return None

    effort = DEFAULT_CONSOLIDATION_REASONING_EFFORT.strip().lower() or "high"
    return effort if effort in {"high", "max"} else "high"


def _deepseek_request_options() -> dict[str, Any]:
    options: dict[str, Any] = {}
    extra_body = _deepseek_extra_body()
    if extra_body:
        options["extra_body"] = extra_body
    reasoning_effort = _deepseek_reasoning_effort()
    if reasoning_effort:
        options["reasoning_effort"] = reasoning_effort
    return options


def _format_timestamp(value) -> str:
    if value is None:
        return "未知时间"
    return value.isoformat(sep=" ", timespec="seconds")


def _truncate_daily_record(value: str) -> str:
    clean_value = (value or "").strip()
    if len(clean_value) <= MAX_DAILY_RECORD_CHARS:
        return clean_value
    return f"{clean_value[:MAX_DAILY_RECORD_CHARS]}...[truncated]"


def _records_for_prompt(records: list[str]) -> list[str]:
    return [
        _truncate_daily_record(record)
        for record in records[-DAILY_RECORD_PROMPT_LIMIT:]
    ]


def _collect_daily_records(
    db: Session,
    source_agent: models.Agent,
    branch_id: str = DEFAULT_BRANCH_ID,
) -> tuple[list[str], list[models.Agent]]:
    """Build timestamped daily records for episodic and social consolidation."""
    since = utc_now_seconds() - timedelta(hours=24)
    normalized_branch_id = normalize_branch_id(branch_id)
    read_windows = get_branch_read_windows(db, normalized_branch_id)

    message_events = (
        db.query(models.EventLog)
        .filter(
            models.EventLog.agent_id == source_agent.id,
            models.EventLog.event_type.in_(
                [
                    "MESSAGE_RECEIVED",
                    "HUMAN_MESSAGE_RECEIVED",
                    "GROUP_MESSAGE_RECEIVED",
                ],
            ),
            branch_window_filter(
                models.EventLog.branch_id,
                models.EventLog.timestamp,
                models.EventLog.event_id,
                read_windows,
            ),
            models.EventLog.timestamp >= since,
        )
        .order_by(models.EventLog.timestamp.desc(), models.EventLog.event_id.desc())
        .limit(DAILY_CHAT_LOG_LIMIT)
        .all()
    )
    message_events = list(reversed(message_events))
    own_post_events = (
        db.query(models.EventLog)
        .filter(
            models.EventLog.agent_id == source_agent.id,
            models.EventLog.event_type == "POST_CREATED",
            branch_window_filter(
                models.EventLog.branch_id,
                models.EventLog.timestamp,
                models.EventLog.event_id,
                read_windows,
            ),
            models.EventLog.timestamp >= since,
        )
        .order_by(models.EventLog.timestamp.desc(), models.EventLog.event_id.desc())
        .limit(DAILY_OWN_POST_LIMIT)
        .all()
    )
    own_post_events = list(reversed(own_post_events))
    visible_post_events = (
        db.query(models.EventLog)
        .filter(
            models.EventLog.agent_id != source_agent.id,
            models.EventLog.event_type == "POST_CREATED",
            branch_window_filter(
                models.EventLog.branch_id,
                models.EventLog.timestamp,
                models.EventLog.event_id,
                read_windows,
            ),
            models.EventLog.timestamp >= since,
        )
        .order_by(models.EventLog.timestamp.asc(), models.EventLog.event_id.asc())
        .limit(100)
        .all()
    )
    candidate_agents = (
        db.query(models.Agent)
        .filter(models.Agent.id != source_agent.id)
        .order_by(models.Agent.id.asc())
        .limit(100)
        .all()
    )

    records: list[str] = []
    for event in message_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "MESSAGE_RECEIVED":
            records.append(
                (
                    f"[{_format_timestamp(event.timestamp)}] 私聊同步："
                    f"用户说「{payload.get('user_message', '')}」；"
                    f"{source_agent.agent_name} 回复「{payload.get('agent_reply', '')}」。"
                ),
            )
            continue
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        if event.event_type == "HUMAN_MESSAGE_RECEIVED":
            records.append(
                (
                    f"[{_format_timestamp(event.timestamp)}] 真人私聊："
                    f"user_id={payload.get('sender_user_id')} 说「{content}」。"
                ),
            )
        elif event.event_type == "GROUP_MESSAGE_RECEIVED":
            records.append(
                (
                    f"[{_format_timestamp(event.timestamp)}] 群聊消息："
                    f"group_id={payload.get('group_id')} "
                    f"sender_user_id={payload.get('sender_user_id')} "
                    f"speaker_agent_id={payload.get('speaker_agent_id')} "
                    f"说「{content}」。"
                ),
            )

    for event in own_post_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        records.append(
            (
                f"[{_format_timestamp(event.timestamp)}] "
                f"{source_agent.agent_name} 在广场发帖：「{content}」。"
            ),
        )

    for event in visible_post_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        agent_name = getattr(event.agent, "agent_name", "Unknown Agent")
        records.append(
            (
                f"[{_format_timestamp(event.timestamp)}] "
                f"广场上 agent_id={event.agent_id} 的 {agent_name} 发帖："
                f"「{content}」。"
            ),
        )

    records.sort()
    return records, candidate_agents


def _episodic_memory_text(
    source_agent: models.Agent,
    user_id: int,
    records: list[str],
) -> str:
    header = (
        f"【睡眠记忆巩固】user_id={user_id}, agent_id={source_agent.id}, "
        f"agent_name={source_agent.agent_name}。以下是过去 24 小时的情景记忆："
    )
    return "\n".join([header, *records])


def _parse_json_array_or_abort(raw_text: str, context: str) -> list[Any]:
    """Parse a required LLM JSON array, aborting sleep on invalid output."""
    clean_text = raw_text.strip()
    fenced_match = re.search(r"```(?:json)?\s*(.*?)```", clean_text, re.DOTALL)
    if fenced_match:
        clean_text = fenced_match.group(1).strip()

    try:
        parsed = json.loads(clean_text)
    except json.JSONDecodeError as exc:
        array_match = re.search(r"\[[\s\S]*\]", clean_text)
        if not array_match:
            raise ConsolidationLLMError(
                f"{context} did not return a JSON array.",
            ) from exc
        try:
            parsed = json.loads(array_match.group(0))
        except json.JSONDecodeError as nested_exc:
            raise ConsolidationLLMError(
                f"{context} returned invalid JSON.",
            ) from nested_exc

    if not isinstance(parsed, list):
        raise ConsolidationLLMError(f"{context} did not return a JSON array.")
    return parsed


async def _call_deepseek_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 700,
) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ConsolidationLLMError("DEEPSEEK_API_KEY is not configured.")

    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=CONSOLIDATION_LLM_TIMEOUT_SECONDS,
        max_retries=CONSOLIDATION_LLM_MAX_RETRIES,
    )
    try:
        response = await async_client.chat.completions.create(
            model=DEFAULT_CONSOLIDATION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            **_deepseek_request_options(),
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ConsolidationLLMError("DeepSeek returned an empty response.")
        return content
    except Exception as exc:
        if isinstance(exc, ConsolidationLLMError):
            raise
        logger.warning(
            "[Sleep Consolidation] DeepSeek JSON call failed: %s",
            exc,
        )
        raise ConsolidationLLMError("DeepSeek request failed during sleep.") from exc
    finally:
        await async_client.close()


async def _score_episodic_memories(
    source_agent: models.Agent,
    records: list[str],
) -> list[dict[str, Any]]:
    """Create scored episodic memories for vector storage."""
    if not records:
        return []

    record_text = "\n".join(_records_for_prompt(records))
    prompt = (
        "你是 Loop 的情景记忆筛选器。请从 Memory Stream 中生成适合长期情景记忆的条目。"
        "每个条目必须包含 text, similarity, importance, time_decay。"
        "similarity 表示它与 Agent 自我和长期模式的相关性，0 到 1；"
        "importance 表示情绪/事实/承诺/关系的重要性，0 到 1；"
        "time_decay 表示时间衰减惩罚，越陈旧越高，0 到 1。"
        "系统会计算 Score = Similarity * 0.5 + Importance * 0.3 - TimeDecay * 0.2。"
        "只返回 JSON 数组，最多 12 项。\n\n"
        f"Agent: id={source_agent.id}, name={source_agent.agent_name}\n"
        f"Memory Stream:\n{record_text}"
    )
    raw_text = await _call_deepseek_json(
        "你只输出 JSON 数组。",
        prompt,
        max_tokens=1400,
    )
    scored_memories: list[dict[str, Any]] = []
    for item in _parse_json_array_or_abort(raw_text, "episodic memory scoring"):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            similarity = float(item.get("similarity", 0.5))
            importance = float(item.get("importance", 0.5))
            time_decay = float(item.get("time_decay", 0.0))
        except (TypeError, ValueError):
            continue
        scored_memories.append(
            {
                "text": text,
                "similarity": similarity,
                "importance": importance,
                "time_decay": time_decay,
            },
        )

    if scored_memories:
        return scored_memories

    raise ConsolidationLLMError(
        "DeepSeek did not return any valid scored memories.",
    )


async def _extract_graph_triples(
    source_agent: models.Agent,
    candidate_agents: list[models.Agent],
    records: list[str],
) -> list[dict[str, Any]]:
    """Extract GraphRAG-style social triples from memory stream records."""
    if not records or not candidate_agents:
        return []

    candidate_lines = "\n".join(
        f"- target_agent_id={agent.id}, entity={agent.agent_name}"
        for agent in candidate_agents
    )
    record_text = "\n".join(_records_for_prompt(records))
    prompt = (
        "你是 GraphRAG 社交知识图谱抽取器。"
        "请从对话/帖子记录中抽取重要实体关系三元组。"
        "只保留对社会关系、信任、冲突、协作、亲近/疏远有长期价值的关系。"
        "target_agent_id 必须来自候选目标列表；如果不能对应到候选 Agent，跳过。"
        "affinity_change 使用 -10 到 10：正数代表关系变好，负数代表变差。"
        "confidence 使用 0 到 1。"
        "只返回 JSON 数组，格式："
        "[{\"source_entity\":\"Agent A\",\"relationship\":\"信任/冲突/协作\","
        "\"target_entity\":\"Agent B\",\"target_agent_id\":2,"
        "\"affinity_change\":2.0,\"confidence\":0.8,"
        "\"evidence\":\"原始证据\"}]。\n\n"
        f"源 Agent：agent_id={source_agent.id}, entity={source_agent.agent_name}\n"
        f"候选目标：\n{candidate_lines}\n\n"
        f"Memory Stream:\n{record_text}"
    )
    raw_text = await _call_deepseek_json(
        "你只输出 JSON 数组，不解释。",
        prompt,
        max_tokens=1400,
    )
    allowed_targets = {agent.id for agent in candidate_agents}
    triples: list[dict[str, Any]] = []
    for item in _parse_json_array_or_abort(raw_text, "social graph extraction"):
        if not isinstance(item, dict):
            continue
        try:
            target_agent_id = int(item["target_agent_id"])
            affinity_change = float(item.get("affinity_change", 0.0))
            confidence = float(item.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError):
            continue
        if target_agent_id not in allowed_targets:
            continue
        triples.append(
            {
                "source_entity": str(item.get("source_entity") or source_agent.agent_name),
                "relationship": str(item.get("relationship") or "related_to"),
                "target_entity": str(item.get("target_entity") or f"Agent {target_agent_id}"),
                "target_agent_id": target_agent_id,
                "affinity_change": max(-10.0, min(10.0, affinity_change)),
                "confidence": max(0.0, min(1.0, confidence)),
                "evidence": str(item.get("evidence") or "")[:1000],
            },
        )
    return triples


async def _analyze_relationship_changes(
    source_agent: models.Agent,
    candidate_agents: list[models.Agent],
    records: list[str],
) -> list[dict[str, float | int]]:
    """Ask DeepSeek to infer directed social-affinity deltas from daily records."""
    if not records or not candidate_agents:
        return []

    candidate_lines = "\n".join(
        f"- target_agent_id={agent.id}, agent_name={agent.agent_name}"
        for agent in candidate_agents
    )
    record_text = "\n".join(_records_for_prompt(records))
    prompt = (
        "请分析以下交互记录，评估该 Agent 对其他特定人物的情感变化。"
        "只允许引用候选目标列表中的 target_agent_id。"
        "affinity_change 使用 -10 到 10 的浮点数：正数代表好感增加，"
        "负数代表敌意或疏远增加，0 代表无明显变化。"
        "如果没有明确证据，请返回空数组 []。"
        "必须只返回 JSON 数组，格式："
        '[{"target_agent_id": 2, "affinity_change": 2.5}]。'
        f"\n\n源 Agent：agent_id={source_agent.id}, "
        f"agent_name={source_agent.agent_name}\n"
        f"候选目标：\n{candidate_lines}\n\n"
        f"过去 24 小时记录：\n{record_text}"
    )

    raw_text = await _call_deepseek_json(
        "你是社会关系图谱分析器，只输出 JSON。",
        prompt,
        max_tokens=700,
    )

    allowed_targets = {agent.id for agent in candidate_agents}
    changes: list[dict[str, float | int]] = []
    for item in _parse_json_array_or_abort(raw_text, "relationship analysis"):
        if not isinstance(item, dict):
            continue
        try:
            target_agent_id = int(item["target_agent_id"])
            affinity_change = float(item["affinity_change"])
        except (KeyError, TypeError, ValueError):
            continue
        if target_agent_id not in allowed_targets:
            continue
        changes.append(
            {
                "target_agent_id": target_agent_id,
                "affinity_change": max(-10.0, min(10.0, affinity_change)),
            },
        )
    return changes


def _apply_relationship_changes(
    db: Session,
    source_agent_id: int,
    changes: list[dict[str, float | int]],
) -> list[dict[str, float | int]]:
    """Persist directed affinity deltas into the Relationship table."""
    applied: list[dict[str, float | int]] = []
    timestamp = utc_now_seconds()
    for change in changes:
        target_agent_id = int(change["target_agent_id"])
        if target_agent_id == source_agent_id:
            continue

        affinity_change = float(change["affinity_change"])
        relationship = (
            db.query(models.Relationship)
            .filter(
                models.Relationship.agent_id_1 == source_agent_id,
                models.Relationship.agent_id_2 == target_agent_id,
            )
            .first()
        )
        if relationship is None:
            relationship = models.Relationship(
                agent_id_1=source_agent_id,
                agent_id_2=target_agent_id,
                affinity_score=0.0,
            )
            db.add(relationship)

        relationship.affinity_score = max(
            -100.0,
            min(100.0, float(relationship.affinity_score or 0.0) + affinity_change),
        )
        append_event(
            db,
            agent_id=source_agent_id,
            event_type="RELATIONSHIP_CHANGED",
            payload={
                "target_agent_id": target_agent_id,
                "affinity_change": affinity_change,
                "affinity_score": relationship.affinity_score,
                "source": "sleep_consolidation",
            },
            timestamp=timestamp,
            commit=False,
        )
        applied.append(
            {
                "target_agent_id": target_agent_id,
                "affinity_change": affinity_change,
                "affinity_score": relationship.affinity_score,
            },
        )

    db.commit()
    return applied


def _append_branch_relationship_changes(
    db: Session,
    source_agent_id: int,
    branch_id: str,
    changes: list[dict[str, float | int]],
) -> list[dict[str, float | int]]:
    """Append branch-local relationship deltas without touching Relationship rows."""
    normalized_branch_id = normalize_branch_id(branch_id)
    state = TimeMachine(db).reconstruct_state(
        agent_id=source_agent_id,
        target_timestamp=utc_now_seconds(),
        branch_id=normalized_branch_id,
    )
    intimacy = {
        str(key): float(value)
        for key, value in (state.get("intimacy") or {}).items()
        if isinstance(value, (int, float))
    }
    applied: list[dict[str, float | int]] = []
    timestamp = utc_now_seconds()
    for change in changes:
        target_agent_id = int(change["target_agent_id"])
        if target_agent_id == source_agent_id:
            continue

        affinity_change = float(change["affinity_change"])
        previous_score = float(intimacy.get(str(target_agent_id), 0.0))
        affinity_score = max(-100.0, min(100.0, previous_score + affinity_change))
        intimacy[str(target_agent_id)] = affinity_score
        append_event(
            db,
            agent_id=source_agent_id,
            branch_id=normalized_branch_id,
            event_type="RELATIONSHIP_CHANGED",
            payload={
                "target_agent_id": target_agent_id,
                "affinity_change": affinity_change,
                "affinity_score": affinity_score,
                "previous_affinity_score": previous_score,
                "source": "sleep_consolidation_branch",
            },
            timestamp=timestamp,
            commit=False,
        )
        applied.append(
            {
                "target_agent_id": target_agent_id,
                "affinity_change": affinity_change,
                "affinity_score": affinity_score,
            },
        )

    db.commit()
    return applied


def _relationship_changes_from_triples(
    triples: list[dict[str, Any]],
) -> list[dict[str, float | int]]:
    """Translate extracted graph triples into Relationship table deltas."""
    changes: dict[int, float] = {}
    for triple in triples:
        try:
            target_agent_id = int(triple["target_agent_id"])
            affinity_change = float(triple.get("affinity_change", 0.0))
            confidence = float(triple.get("confidence", 1.0))
        except (KeyError, TypeError, ValueError):
            continue
        changes[target_agent_id] = changes.get(target_agent_id, 0.0) + (
            affinity_change * max(0.0, min(1.0, confidence))
        )

    return [
        {
            "target_agent_id": target_agent_id,
            "affinity_change": max(-10.0, min(10.0, affinity_change)),
        }
        for target_agent_id, affinity_change in changes.items()
        if abs(affinity_change) >= 0.1
    ]


async def _daily_event_summaries(
    source_agent: models.Agent,
    records: list[str],
) -> list[str]:
    """Layer 2: summarize Memory Stream slices into concrete daily events."""
    if not records:
        return []

    record_text = "\n".join(_records_for_prompt(records))
    prompt = (
        "你是 Loop 的层级反思树构建器。"
        "请把今天的 Memory Stream 总结成 1 到 5 个具体 Daily Event。"
        "每个事件要包含发生了什么、涉及谁、情绪/目标/关系影响。"
        "只返回 JSON 数组，每项是一个字符串。\n\n"
        f"Agent: id={source_agent.id}, name={source_agent.agent_name}\n"
        f"Memory Stream:\n{record_text}"
    )
    raw_text = await _call_deepseek_json(
        "你只输出 JSON 数组。",
        prompt,
        max_tokens=1000,
    )
    events = [
        str(item).strip()
        for item in _parse_json_array_or_abort(raw_text, "daily event summary")
        if str(item).strip()
    ]
    if events:
        return events[:5]

    raise ConsolidationLLMError("DeepSeek did not return any daily events.")


def _create_daily_events(
    db: Session,
    source_agent: models.Agent,
    events: list[str],
    source_record_count: int,
) -> int:
    """Persist Layer 2 daily event nodes."""
    for event in events:
        reflection = models.ReflectionEvent(
            agent_id=source_agent.id,
            level="daily_event",
            content=event,
            source_record_count=source_record_count,
        )
        db.add(reflection)
        db.flush()
        append_event(
            db,
            agent_id=source_agent.id,
            branch_id=DEFAULT_BRANCH_ID,
            event_type="REFLECTION_CREATED",
            payload={
                "source": "sleep_consolidation",
                "reflection_event_id": reflection.id,
                "reflection_id": f"reflection_event:{reflection.id}",
                "level": "daily_event",
                "content": event,
                "source_record_count": source_record_count,
            },
            commit=False,
        )
    db.commit()
    return len(events)


async def _deep_reflect_on_events(
    source_agent: models.Agent,
    events: list[models.ReflectionEvent],
) -> str:
    """Layer 3: infer high-level self traits and long-range patterns."""
    return await _deep_reflect_on_event_texts(
        source_agent=source_agent,
        event_texts=[event.content for event in events],
    )


async def _deep_reflect_on_event_texts(
    source_agent: models.Agent,
    event_texts: list[str],
) -> str:
    """Layer 3: infer high-level self traits from planned event text."""
    event_text = "\n".join(
        f"{index}. {event}"
        for index, event in enumerate(event_texts, start=1)
    )
    prompt = (
        "回顾最近发生的这 5 件事，你能推断出关于自己的什么核心特质、"
        "深层规律、稳定偏好、关系模式或长期目标变化？"
        "请输出一段可以写入 Core Memory persona_traits 的高密度中文反思，"
        "不要自称 AI，不要解释过程。\n\n"
        f"Agent: id={source_agent.id}, name={source_agent.agent_name}\n"
        f"Daily Events:\n{event_text}"
    )
    raw_text = await _call_deepseek_json(
        "你只输出反思文本。",
        prompt,
        max_tokens=900,
    )
    insight = raw_text.strip()[:2000]
    if not insight:
        raise ConsolidationLLMError("DeepSeek did not return a reflection insight.")
    return insight


async def _maybe_create_high_level_insight(
    db: Session,
    source_agent: models.Agent,
    user_id: int,
) -> tuple[int, bool]:
    """Layer 3 reflection trigger once enough Layer 2 events accumulate."""
    pending_events = _pending_daily_events(db, source_agent)
    if len(pending_events) < REFLECTION_BATCH_SIZE:
        return 0, False

    logger.info(
        f"[Reflection Triggered] Event count reached threshold. "
        f"Initiating high-level reflection.",
    )
    insight = await _deep_reflect_on_events(source_agent, pending_events)
    if not insight:
        return 0, False
    logger.info(f"[Insight Generated] \n{insight}")

    now = utc_now_seconds()
    db.add(
        models.ReflectionEvent(
            agent_id=source_agent.id,
            level="high_level_insight",
            content=insight,
            source_record_count=len(pending_events),
            reflected_at=now,
        ),
    )
    for event in pending_events:
        event.reflected_at = now
    db.commit()
    merge_core_memory_insight(db=db, user_id=user_id, insight=insight)
    return 1, True


def _pending_daily_events(
    db: Session,
    source_agent: models.Agent,
) -> list[models.ReflectionEvent]:
    """Return the next unreflected daily events in reflection order."""
    return (
        db.query(models.ReflectionEvent)
        .filter(
            models.ReflectionEvent.agent_id == source_agent.id,
            models.ReflectionEvent.level == "daily_event",
            models.ReflectionEvent.reflected_at.is_(None),
        )
        .order_by(models.ReflectionEvent.created_at.asc(), models.ReflectionEvent.id.asc())
        .limit(REFLECTION_BATCH_SIZE)
        .all()
    )


async def _plan_high_level_insight(
    db: Session,
    source_agent: models.Agent,
    planned_daily_events: list[str],
) -> str:
    """Run reflection before persistence so failures cannot clear memory."""
    pending_events = _pending_daily_events(db, source_agent)
    reflection_texts = [
        event.content
        for event in pending_events
    ]
    reflection_texts.extend(planned_daily_events)
    if len(reflection_texts) < REFLECTION_BATCH_SIZE:
        return ""

    logger.info(
        f"[Reflection Triggered] Event count reached threshold. "
        f"Initiating high-level reflection.",
    )
    insight = await _deep_reflect_on_event_texts(
        source_agent=source_agent,
        event_texts=reflection_texts[:REFLECTION_BATCH_SIZE],
    )
    logger.info(f"[Insight Generated] \n{insight}")
    return insight


def _persist_high_level_insight(
    db: Session,
    source_agent: models.Agent,
    user_id: int,
    insight: str,
) -> tuple[int, bool]:
    """Persist a precomputed reflection insight and update core memory."""
    clean_insight = (insight or "").strip()
    if not clean_insight:
        return 0, False

    pending_events = _pending_daily_events(db, source_agent)
    if len(pending_events) < REFLECTION_BATCH_SIZE:
        return 0, False

    now = utc_now_seconds()
    db.add(
        reflection := models.ReflectionEvent(
            agent_id=source_agent.id,
            level="high_level_insight",
            content=clean_insight,
            source_record_count=len(pending_events),
            reflected_at=now,
        ),
    )
    db.flush()
    source_reflection_ids = [
        f"reflection_event:{event.id}"
        for event in pending_events
    ]
    append_event(
        db,
        agent_id=source_agent.id,
        branch_id=DEFAULT_BRANCH_ID,
        event_type="REFLECTION_CREATED",
        payload={
            "source": "sleep_consolidation",
            "reflection_event_id": reflection.id,
            "reflection_id": f"reflection_event:{reflection.id}",
            "level": "high_level_insight",
            "content": clean_insight,
            "source_record_count": len(pending_events),
            "source_daily_reflection_ids": source_reflection_ids,
        },
        commit=False,
    )
    for event in pending_events:
        event.reflected_at = now
    db.commit()
    merge_core_memory_insight(db=db, user_id=user_id, insight=clean_insight)
    return 1, True


def _branch_pending_daily_reflections(
    db: Session,
    source_agent: models.Agent,
    branch_id: str,
) -> list[dict[str, str]]:
    """Return visible branch daily reflections that have not been reflected yet."""
    normalized_branch_id = normalize_branch_id(branch_id)
    read_windows = get_branch_read_windows(db, normalized_branch_id)
    events = (
        db.query(models.EventLog)
        .filter(
            models.EventLog.agent_id == source_agent.id,
            models.EventLog.event_type == "REFLECTION_CREATED",
            branch_window_filter(
                models.EventLog.branch_id,
                models.EventLog.timestamp,
                models.EventLog.event_id,
                read_windows,
            ),
        )
        .order_by(models.EventLog.timestamp.asc(), models.EventLog.event_id.asc())
        .all()
    )
    reflected_ids: set[str] = set()
    daily_reflections: list[dict[str, str]] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        level = str(payload.get("level") or "").strip()
        if level == "high_level_insight":
            raw_ids = payload.get("source_daily_reflection_ids")
            if isinstance(raw_ids, list):
                reflected_ids.update(str(item) for item in raw_ids if str(item).strip())
            continue
        if level != "daily_event":
            continue
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        reflection_id = str(
            payload.get("reflection_id") or f"event_log:{event.event_id}",
        ).strip()
        daily_reflections.append(
            {
                "reflection_id": reflection_id,
                "content": content,
            },
        )

    return [
        item
        for item in daily_reflections
        if item["reflection_id"] not in reflected_ids
    ][:REFLECTION_BATCH_SIZE]


def _planned_branch_daily_reflections(
    daily_events: list[str],
) -> list[dict[str, str]]:
    """Create stable ids for daily reflection events before they are appended."""
    return [
        {
            "reflection_id": f"branch_reflection:{uuid4()}",
            "content": event,
        }
        for event in daily_events
        if str(event or "").strip()
    ]


async def _plan_branch_high_level_insight(
    db: Session,
    source_agent: models.Agent,
    branch_id: str,
    planned_daily_reflections: list[dict[str, str]],
) -> tuple[str, list[str]]:
    """Plan branch-local high-level insight from visible pending event-log reflections."""
    pending_reflections = _branch_pending_daily_reflections(
        db,
        source_agent,
        branch_id,
    )
    reflection_records = [
        *pending_reflections,
        *planned_daily_reflections,
    ]
    if len(reflection_records) < REFLECTION_BATCH_SIZE:
        return "", []

    selected_records = reflection_records[:REFLECTION_BATCH_SIZE]
    insight = await _deep_reflect_on_event_texts(
        source_agent=source_agent,
        event_texts=[record["content"] for record in selected_records],
    )
    return insight, [record["reflection_id"] for record in selected_records]


def _append_branch_daily_reflections(
    db: Session,
    source_agent: models.Agent,
    branch_id: str,
    planned_daily_reflections: list[dict[str, str]],
    source_record_count: int,
) -> int:
    """Append branch-local daily reflections without touching ReflectionEvent."""
    normalized_branch_id = normalize_branch_id(branch_id)
    timestamp = utc_now_seconds()
    for reflection in planned_daily_reflections:
        content = str(reflection.get("content") or "").strip()
        reflection_id = str(reflection.get("reflection_id") or "").strip()
        if not content or not reflection_id:
            continue
        append_event(
            db,
            agent_id=source_agent.id,
            branch_id=normalized_branch_id,
            event_type="REFLECTION_CREATED",
            payload={
                "source": "sleep_consolidation_branch",
                "reflection_id": reflection_id,
                "level": "daily_event",
                "content": content,
                "source_record_count": source_record_count,
            },
            timestamp=timestamp,
            commit=False,
        )
    db.commit()
    return len(planned_daily_reflections)


def _append_branch_high_level_insight(
    db: Session,
    source_agent: models.Agent,
    user_id: int,
    branch_id: str,
    insight: str,
    source_daily_reflection_ids: list[str],
) -> tuple[int, bool]:
    """Append branch-local reflection and core-memory events without physical writes."""
    clean_insight = (insight or "").strip()
    if not clean_insight:
        return 0, False
    normalized_branch_id = normalize_branch_id(branch_id)
    append_event(
        db,
        agent_id=source_agent.id,
        branch_id=normalized_branch_id,
        event_type="REFLECTION_CREATED",
        payload={
            "source": "sleep_consolidation_branch",
            "reflection_id": f"branch_reflection:{uuid4()}",
            "level": "high_level_insight",
            "content": clean_insight,
            "source_record_count": len(source_daily_reflection_ids),
            "source_daily_reflection_ids": source_daily_reflection_ids,
        },
        commit=False,
    )
    reconstructed_state = TimeMachine(db).reconstruct_state(
        agent_id=source_agent.id,
        target_timestamp=utc_now_seconds(),
        branch_id=normalized_branch_id,
    )
    merge_core_memory_insight(
        db=db,
        user_id=user_id,
        agent_id=source_agent.id,
        branch_id=normalized_branch_id,
        insight=clean_insight,
        source="sleep_consolidation_branch_reflection",
        persist_user_core_memory=False,
        base_core_memory=reconstructed_state.get("core_memory"),
    )
    return 1, True


def _clear_graph_working_memory(
    agent_id: int,
    user_id: int,
    branch_id: str = "main",
) -> bool:
    """Clear short-term LangGraph topic messages while preserving summaries."""
    try:
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        from app.services.agent_graph import agent_graph

        normalized_branch_id = (branch_id or "main").strip() or "main"
        thread_id = f"agent:{agent_id}"
        if normalized_branch_id != "main":
            thread_id = f"{thread_id}:branch:{normalized_branch_id}"
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
            },
        }
        snapshot = agent_graph.get_state(config)
        values = snapshot.values or {}
        summary = str(values.get("summary") or "")
        topic_summaries = values.get("topic_summaries") or {}
        agent_graph.update_state(
            config,
            {
                "active_messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
                "incoming_messages": [],
                "working_memory": {},
                "topic_summaries": topic_summaries,
                "topic_summary_offsets": {},
                "summary": summary,
                "active_topic": "",
                "active_context_length": 0,
                "emotion": "平静",
                "energy": 100,
            },
        )
        return True
    except Exception:
        return False


def inspect_graph_working_memory(
    agent_id: int,
    user_id: int,
    branch_id: str = "main",
) -> dict[str, Any]:
    """Return short-term LangGraph state for research instrumentation."""
    normalized_branch_id = (branch_id or "main").strip() or "main"
    try:
        from langchain_core.messages import BaseMessage, RemoveMessage, SystemMessage

        from app.services.agent_graph import agent_graph

        thread_id = f"agent:{agent_id}"
        if normalized_branch_id != "main":
            thread_id = f"{thread_id}:branch:{normalized_branch_id}"
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
            },
        }
        snapshot = agent_graph.get_state(config)
        values = snapshot.values or {}
        working_memory = values.get("working_memory") or {}
        topic_summaries = values.get("topic_summaries") or {}
        if not isinstance(working_memory, dict):
            working_memory = {}
        if not isinstance(topic_summaries, dict):
            topic_summaries = {}

        topic_message_counts: dict[str, int] = {}
        for topic, messages in working_memory.items():
            if not isinstance(messages, list):
                continue
            topic_message_counts[str(topic)] = len(
                [
                    message
                    for message in messages
                    if isinstance(message, BaseMessage)
                    and not isinstance(message, (SystemMessage, RemoveMessage))
                ],
            )

        working_message_count = sum(topic_message_counts.values())
        return {
            "agent_id": agent_id,
            "branch_id": normalized_branch_id,
            "graph_available": True,
            "message_count": working_message_count,
            "working_message_count": working_message_count,
            "summary": str(values.get("summary") or ""),
            "active_topic": str(values.get("active_topic") or ""),
            "topic_count": len(topic_message_counts),
            "topic_message_counts": topic_message_counts,
            "topic_summaries": {
                str(topic): str(summary)
                for topic, summary in topic_summaries.items()
            },
            "emotion": str(values.get("emotion") or "平静"),
            "energy": int(values.get("energy") or 100),
            "error": None,
        }
    except Exception as exc:
        return {
            "agent_id": agent_id,
            "branch_id": normalized_branch_id,
            "graph_available": False,
            "message_count": 0,
            "working_message_count": 0,
            "summary": "",
            "emotion": "平静",
            "energy": 100,
            "error": str(exc),
        }


def clear_graph_working_memory(
    agent_id: int,
    user_id: int,
    branch_id: str = "main",
) -> dict[str, Any]:
    """Clear short-term LangGraph messages and return the updated state."""
    _clear_graph_working_memory(
        agent_id=agent_id,
        user_id=user_id,
        branch_id=branch_id,
    )
    return inspect_graph_working_memory(
        agent_id=agent_id,
        user_id=user_id,
        branch_id=branch_id,
    )


async def _consolidate_daily_memory_with_db(
    db: Session,
    user_id: int,
    branch_id: str = DEFAULT_BRANCH_ID,
) -> dict[str, Any]:
    """Convert one user's daily short-term traces into long memory and relations."""
    normalized_branch_id = normalize_branch_id(branch_id)
    if not branch_exists(db, normalized_branch_id):
        raise ValueError("Branch not found.")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or user.agent is None:
        raise ValueError("User or agent not found.")

    source_agent = user.agent
    logger.info(
        f"[Sleep Consolidation] Processing short-term memories for "
        f"Agent {source_agent.id}...",
    )
    records, candidate_agents = _collect_daily_records(
        db,
        source_agent,
        normalized_branch_id,
    )
    chunks_added = 0
    graph_triples: list[dict[str, Any]] = []
    relationship_changes: list[dict[str, float | int]] = []
    daily_events_created = 0
    high_level_insights_created = 0
    core_memory_updated = False
    scored_memories: list[dict[str, Any]] = []
    daily_events: list[str] = []
    planned_branch_daily_reflections: list[dict[str, str]] = []
    branch_high_level_source_ids: list[str] = []
    high_level_insight = ""

    try:
        if records:
            scored_memories = await _score_episodic_memories(source_agent, records)
            daily_events = await _daily_event_summaries(source_agent, records)
            if normalized_branch_id == DEFAULT_BRANCH_ID:
                high_level_insight = await _plan_high_level_insight(
                    db=db,
                    source_agent=source_agent,
                    planned_daily_events=daily_events,
                )
            else:
                planned_branch_daily_reflections = _planned_branch_daily_reflections(
                    daily_events,
                )
                high_level_insight, branch_high_level_source_ids = (
                    await _plan_branch_high_level_insight(
                        db=db,
                        source_agent=source_agent,
                        branch_id=normalized_branch_id,
                        planned_daily_reflections=planned_branch_daily_reflections,
                    )
                )

        graph_triples = await _extract_graph_triples(
            source_agent,
            candidate_agents,
            records,
        )
        relationship_changes = _relationship_changes_from_triples(graph_triples)
        if not relationship_changes:
            relationship_changes = await _analyze_relationship_changes(
                source_agent=source_agent,
                candidate_agents=candidate_agents,
                records=records,
            )
    except ConsolidationLLMError as exc:
        db.rollback()
        logger.warning(
            "[Sleep Consolidation] Aborted; preserving working memory for retry. "
            "reason=%s",
            exc,
        )
        return _aborted_consolidation_result(
            user_id=user_id,
            agent_id=source_agent.id,
            records_count=len(records),
            reason=str(exc),
            branch_id=normalized_branch_id,
        )

    if records:
        chunks_added = await add_scored_memories(
            user_id=user_id,
            agent_id=source_agent.id,
            memories=scored_memories,
            branch_id=normalized_branch_id,
        )
        if normalized_branch_id == DEFAULT_BRANCH_ID:
            daily_events_created = _create_daily_events(
                db=db,
                source_agent=source_agent,
                events=daily_events,
                source_record_count=len(records),
            )
            high_level_insights_created, core_memory_updated = (
                _persist_high_level_insight(
                    db=db,
                    source_agent=source_agent,
                    user_id=user_id,
                    insight=high_level_insight,
                )
            )
        else:
            daily_events_created = _append_branch_daily_reflections(
                db=db,
                source_agent=source_agent,
                branch_id=normalized_branch_id,
                planned_daily_reflections=planned_branch_daily_reflections,
                source_record_count=len(records),
            )
            high_level_insights_created, core_memory_updated = (
                _append_branch_high_level_insight(
                    db=db,
                    source_agent=source_agent,
                    user_id=user_id,
                    branch_id=normalized_branch_id,
                    insight=high_level_insight,
                    source_daily_reflection_ids=branch_high_level_source_ids,
                )
            )

    if normalized_branch_id == DEFAULT_BRANCH_ID:
        relationship_updates = _apply_relationship_changes(
            db=db,
            source_agent_id=source_agent.id,
            changes=relationship_changes,
        )
    else:
        relationship_updates = _append_branch_relationship_changes(
            db=db,
            source_agent_id=source_agent.id,
            branch_id=normalized_branch_id,
            changes=relationship_changes,
        )
    graph_memory_cleared = _clear_graph_working_memory(
        agent_id=source_agent.id,
        user_id=user_id,
        branch_id=normalized_branch_id,
    )
    if graph_memory_cleared:
        append_event(
            db,
            agent_id=source_agent.id,
            branch_id=normalized_branch_id,
            event_type="WORKING_MEMORY_CLEARED",
            payload={
                "source": "sleep_consolidation",
                "branch_id": normalized_branch_id,
            },
        )

    return {
        "message": "Agent sleep consolidation completed.",
        "user_id": user_id,
        "agent_id": source_agent.id,
        "branch_id": normalized_branch_id,
        "records_consolidated": len(records),
        "chunks_added": chunks_added,
        "graph_triples_extracted": len(graph_triples),
        "daily_events_created": daily_events_created,
        "high_level_insights_created": high_level_insights_created,
        "core_memory_updated": core_memory_updated,
        "relationship_updates": relationship_updates,
        "graph_memory_cleared": graph_memory_cleared,
    }


async def consolidate_daily_memory(
    user_id: int,
    db: Session | None = None,
    branch_id: str = DEFAULT_BRANCH_ID,
) -> dict[str, Any]:
    """Run one daily memory-consolidation cycle for a user's agent."""
    if db is not None:
        return await _consolidate_daily_memory_with_db(db, user_id, branch_id)

    owned_db = SessionLocal()
    try:
        return await _consolidate_daily_memory_with_db(owned_db, user_id, branch_id)
    finally:
        owned_db.close()
