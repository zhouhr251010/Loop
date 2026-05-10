"""Offline memory consolidation for Loop agents."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.models import utc_now_seconds
from app.services.core_memory_service import merge_core_memory_insight
from app.services.rag_service import add_scored_memories


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEFAULT_THINKING_MODE = os.getenv("DEEPSEEK_THINKING", "enabled")
DEFAULT_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
REFLECTION_BATCH_SIZE = 5


def _deepseek_extra_body() -> dict[str, dict[str, str]]:
    thinking_mode = DEFAULT_THINKING_MODE.strip().lower() or "disabled"
    if thinking_mode not in {"enabled", "disabled"}:
        thinking_mode = "disabled"
    return {"thinking": {"type": thinking_mode}}


def _deepseek_reasoning_effort() -> str | None:
    if (_deepseek_extra_body()["thinking"]["type"]) != "enabled":
        return None

    effort = DEFAULT_REASONING_EFFORT.strip().lower() or "high"
    return effort if effort in {"high", "max"} else "high"


def _format_timestamp(value) -> str:
    if value is None:
        return "未知时间"
    return value.isoformat(sep=" ", timespec="seconds")


def _collect_daily_records(
    db: Session,
    source_agent: models.Agent,
) -> tuple[list[str], list[models.Agent]]:
    """Build timestamped daily records for episodic and social consolidation."""
    since = utc_now_seconds() - timedelta(hours=24)

    chat_logs = (
        db.query(models.ChatLog)
        .filter(
            models.ChatLog.agent_id == source_agent.id,
            models.ChatLog.timestamp >= since,
        )
        .order_by(models.ChatLog.timestamp.asc(), models.ChatLog.id.asc())
        .all()
    )
    own_posts = (
        db.query(models.Post)
        .filter(
            models.Post.agent_id == source_agent.id,
            models.Post.timestamp >= since,
        )
        .order_by(models.Post.timestamp.asc(), models.Post.id.asc())
        .all()
    )
    visible_posts = (
        db.query(models.Post)
        .join(models.Agent)
        .filter(
            models.Post.agent_id != source_agent.id,
            models.Post.timestamp >= since,
        )
        .order_by(models.Post.timestamp.asc(), models.Post.id.asc())
        .limit(100)
        .all()
    )
    candidate_agents = (
        db.query(models.Agent)
        .filter(models.Agent.id != source_agent.id)
        .order_by(models.Agent.id.asc())
        .all()
    )

    records: list[str] = []
    for chat in chat_logs:
        records.append(
            (
                f"[{_format_timestamp(chat.timestamp)}] 私聊同步："
                f"用户说「{chat.user_message}」；"
                f"{source_agent.agent_name} 回复「{chat.agent_reply}」。"
            ),
        )

    for post in own_posts:
        records.append(
            (
                f"[{_format_timestamp(post.timestamp)}] "
                f"{source_agent.agent_name} 在广场发帖：「{post.content}」。"
            ),
        )

    for post in visible_posts:
        agent_name = getattr(post.agent, "agent_name", "Unknown Agent")
        records.append(
            (
                f"[{_format_timestamp(post.timestamp)}] "
                f"广场上 agent_id={post.agent_id} 的 {agent_name} 发帖："
                f"「{post.content}」。"
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


def _extract_json_array(raw_text: str) -> list[Any]:
    clean_text = raw_text.strip()
    fenced_match = re.search(r"```(?:json)?\s*(.*?)```", clean_text, re.DOTALL)
    if fenced_match:
        clean_text = fenced_match.group(1).strip()

    try:
        parsed = json.loads(clean_text)
    except json.JSONDecodeError:
        array_match = re.search(r"\[[\s\S]*\]", clean_text)
        if not array_match:
            return []
        try:
            parsed = json.loads(array_match.group(0))
        except json.JSONDecodeError:
            return []

    return parsed if isinstance(parsed, list) else []


def _call_deepseek_json(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 700,
) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return ""

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=30.0,
        )
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            extra_body=_deepseek_extra_body(),
            reasoning_effort=_deepseek_reasoning_effort(),
        )
        return response.choices[0].message.content or ""
    except Exception:
        return ""


def _score_episodic_memories(
    source_agent: models.Agent,
    records: list[str],
) -> list[dict[str, Any]]:
    """Create scored episodic memories for vector storage."""
    if not records:
        return []

    record_text = "\n".join(records[-120:])
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
    raw_text = _call_deepseek_json(
        "你只输出 JSON 数组。",
        prompt,
        max_tokens=900,
    )
    scored_memories: list[dict[str, Any]] = []
    for item in _extract_json_array(raw_text):
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

    return [
        {
            "text": record,
            "similarity": 0.55,
            "importance": 0.55,
            "time_decay": min(index / max(len(records), 1), 1.0) * 0.2,
        }
        for index, record in enumerate(records[-20:])
    ]


def _extract_graph_triples(
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
    record_text = "\n".join(records[-120:])
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
    raw_text = _call_deepseek_json(
        "你只输出 JSON 数组，不解释。",
        prompt,
        max_tokens=900,
    )
    allowed_targets = {agent.id for agent in candidate_agents}
    triples: list[dict[str, Any]] = []
    for item in _extract_json_array(raw_text):
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


def _analyze_relationship_changes(
    source_agent: models.Agent,
    candidate_agents: list[models.Agent],
    records: list[str],
) -> list[dict[str, float | int]]:
    """Ask DeepSeek to infer directed social-affinity deltas from daily records."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key or not records or not candidate_agents:
        return []

    candidate_lines = "\n".join(
        f"- target_agent_id={agent.id}, agent_name={agent.agent_name}"
        for agent in candidate_agents
    )
    record_text = "\n".join(records[-120:])
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

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=20.0,
        )
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是社会关系图谱分析器，只输出 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=240,
            extra_body=_deepseek_extra_body(),
            reasoning_effort=_deepseek_reasoning_effort(),
        )
        raw_text = response.choices[0].message.content or ""
    except Exception:
        return []

    allowed_targets = {agent.id for agent in candidate_agents}
    changes: list[dict[str, float | int]] = []
    for item in _extract_json_array(raw_text):
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
        applied.append(
            {
                "target_agent_id": target_agent_id,
                "affinity_change": affinity_change,
                "affinity_score": relationship.affinity_score,
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


def _daily_event_summaries(
    source_agent: models.Agent,
    records: list[str],
) -> list[str]:
    """Layer 2: summarize Memory Stream slices into concrete daily events."""
    if not records:
        return []

    record_text = "\n".join(records[-120:])
    prompt = (
        "你是 Loop 的层级反思树构建器。"
        "请把今天的 Memory Stream 总结成 1 到 5 个具体 Daily Event。"
        "每个事件要包含发生了什么、涉及谁、情绪/目标/关系影响。"
        "只返回 JSON 数组，每项是一个字符串。\n\n"
        f"Agent: id={source_agent.id}, name={source_agent.agent_name}\n"
        f"Memory Stream:\n{record_text}"
    )
    raw_text = _call_deepseek_json(
        "你只输出 JSON 数组。",
        prompt,
        max_tokens=700,
    )
    events = [
        str(item).strip()
        for item in _extract_json_array(raw_text)
        if str(item).strip()
    ]
    if events:
        return events[:5]

    return records[-5:]


def _create_daily_events(
    db: Session,
    source_agent: models.Agent,
    events: list[str],
    source_record_count: int,
) -> int:
    """Persist Layer 2 daily event nodes."""
    for event in events:
        db.add(
            models.ReflectionEvent(
                agent_id=source_agent.id,
                level="daily_event",
                content=event,
                source_record_count=source_record_count,
            ),
        )
    db.commit()
    return len(events)


def _deep_reflect_on_events(
    source_agent: models.Agent,
    events: list[models.ReflectionEvent],
) -> str:
    """Layer 3: infer high-level self traits and long-range patterns."""
    event_text = "\n".join(
        f"{index}. {event.content}"
        for index, event in enumerate(events, start=1)
    )
    prompt = (
        "回顾最近发生的这 5 件事，你能推断出关于自己的什么核心特质、"
        "深层规律、稳定偏好、关系模式或长期目标变化？"
        "请输出一段可以写入 Core Memory persona_traits 的高密度中文反思，"
        "不要自称 AI，不要解释过程。\n\n"
        f"Agent: id={source_agent.id}, name={source_agent.agent_name}\n"
        f"Daily Events:\n{event_text}"
    )
    raw_text = _call_deepseek_json(
        "你只输出反思文本。",
        prompt,
        max_tokens=420,
    )
    return raw_text.strip()[:2000]


def _maybe_create_high_level_insight(
    db: Session,
    source_agent: models.Agent,
    user_id: int,
) -> tuple[int, bool]:
    """Layer 3 reflection trigger once enough Layer 2 events accumulate."""
    pending_events = (
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
    if len(pending_events) < REFLECTION_BATCH_SIZE:
        return 0, False

    logger.info(
        f"[Reflection Triggered] Event count reached threshold. "
        f"Initiating high-level reflection.",
    )
    insight = _deep_reflect_on_events(source_agent, pending_events)
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


def _clear_graph_working_memory(
    agent_id: int,
    user_id: int,
) -> bool:
    """Clear short-term LangGraph topic messages while preserving summaries."""
    try:
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        from app.services.agent_graph import agent_graph

        config = {
            "configurable": {
                "thread_id": f"agent:{agent_id}",
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


def inspect_graph_working_memory(agent_id: int, user_id: int) -> dict[str, Any]:
    """Return short-term LangGraph state for research instrumentation."""
    try:
        from langchain_core.messages import BaseMessage, RemoveMessage, SystemMessage

        from app.services.agent_graph import agent_graph

        config = {
            "configurable": {
                "thread_id": f"agent:{agent_id}",
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
            "graph_available": False,
            "message_count": 0,
            "working_message_count": 0,
            "summary": "",
            "emotion": "平静",
            "energy": 100,
            "error": str(exc),
        }


def clear_graph_working_memory(agent_id: int, user_id: int) -> dict[str, Any]:
    """Clear short-term LangGraph messages and return the updated state."""
    _clear_graph_working_memory(agent_id=agent_id, user_id=user_id)
    return inspect_graph_working_memory(agent_id=agent_id, user_id=user_id)


def _consolidate_daily_memory_with_db(db: Session, user_id: int) -> dict[str, Any]:
    """Convert one user's daily short-term traces into long memory and relations."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or user.agent is None:
        raise ValueError("User or agent not found.")

    source_agent = user.agent
    logger.info(
        f"[Sleep Consolidation] Processing short-term memories for "
        f"Agent {source_agent.id}...",
    )
    records, candidate_agents = _collect_daily_records(db, source_agent)
    chunks_added = 0
    graph_triples: list[dict[str, Any]] = []
    daily_events_created = 0
    high_level_insights_created = 0
    core_memory_updated = False

    if records:
        chunks_added = add_scored_memories(
            user_id=user_id,
            agent_id=source_agent.id,
            memories=_score_episodic_memories(source_agent, records),
        )
        daily_events = _daily_event_summaries(source_agent, records)
        daily_events_created = _create_daily_events(
            db=db,
            source_agent=source_agent,
            events=daily_events,
            source_record_count=len(records),
        )
        high_level_insights_created, core_memory_updated = (
            _maybe_create_high_level_insight(
                db=db,
                source_agent=source_agent,
                user_id=user_id,
            )
        )

    graph_triples = _extract_graph_triples(source_agent, candidate_agents, records)
    relationship_changes = _relationship_changes_from_triples(graph_triples)
    if not relationship_changes:
        relationship_changes = _analyze_relationship_changes(
            source_agent=source_agent,
            candidate_agents=candidate_agents,
            records=records,
        )
    relationship_updates = _apply_relationship_changes(
        db=db,
        source_agent_id=source_agent.id,
        changes=relationship_changes,
    )
    graph_memory_cleared = _clear_graph_working_memory(
        agent_id=source_agent.id,
        user_id=user_id,
    )

    return {
        "message": "Agent sleep consolidation completed.",
        "user_id": user_id,
        "agent_id": source_agent.id,
        "records_consolidated": len(records),
        "chunks_added": chunks_added,
        "graph_triples_extracted": len(graph_triples),
        "daily_events_created": daily_events_created,
        "high_level_insights_created": high_level_insights_created,
        "core_memory_updated": core_memory_updated,
        "relationship_updates": relationship_updates,
        "graph_memory_cleared": graph_memory_cleared,
    }


def consolidate_daily_memory(
    user_id: int,
    db: Session | None = None,
) -> dict[str, Any]:
    """Run one daily memory-consolidation cycle for a user's agent."""
    if db is not None:
        return _consolidate_daily_memory_with_db(db, user_id)

    owned_db = SessionLocal()
    try:
        return _consolidate_daily_memory_with_db(owned_db, user_id)
    finally:
        owned_db.close()
