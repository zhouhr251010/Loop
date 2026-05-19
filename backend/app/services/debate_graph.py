"""Supervisor-pattern LangGraph flow for multi-agent debates."""

from __future__ import annotations

import json
import logging
import os
from functools import partial
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds
from app.services.branching import normalize_branch_id
from app.services.core_memory_service import format_core_memory_for_prompt
from app.services.event_store import append_event
from app.services.llm_service import build_async_deepseek_client
from app.services.rag_service import retrieve_hybrid_memory
from app.services.time_machine import TimeMachine


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

DEBATE_DEFAULT_MAX_TURNS = 12
DEBATE_ABSOLUTE_MAX_TURNS = 64
DEBATE_RECENT_MESSAGE_LIMIT = 16
DEBATE_MEMORY_TOP_K = 3
DEBATE_GROUP_EVENT = "GROUP_MESSAGE_RECEIVED"
DEBATE_CONCLUDED_EVENT = "DEBATE_CONCLUDED"


class DebateMessage(TypedDict, total=False):
    """One bounded debate message in graph state."""

    role: Literal["system", "moderator", "assistant", "user"]
    speaker_id: int | None
    speaker_name: str
    content: str
    timestamp: str


class DebateState(TypedDict, total=False):
    """State for the debate supervisor graph."""

    messages: list[DebateMessage]
    topic: str
    participants: list[int]
    current_speaker: int | None
    turns_count: int
    max_turns: int
    is_consensus_reached: bool
    final_report: str


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def _debate_model() -> str:
    return os.getenv(
        "LOOP_DEBATE_MODEL",
        os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat"),
    ).strip()


def _debate_timeout_seconds() -> float:
    raw_value = os.getenv("LOOP_DEBATE_LLM_TIMEOUT_SECONDS", "25").strip()
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        return 25.0


def _debate_max_tokens() -> int:
    return max(128, min(_int_env("LOOP_DEBATE_MAX_TOKENS", 900), 2000))


def _safe_max_turns(value: int | None) -> int:
    if value is None:
        return DEBATE_DEFAULT_MAX_TURNS
    return max(1, min(int(value), DEBATE_ABSOLUTE_MAX_TURNS))


def _normalize_participants(participants: list[int] | None) -> list[int]:
    normalized: list[int] = []
    for raw_agent_id in participants or []:
        try:
            agent_id = int(raw_agent_id)
        except (TypeError, ValueError):
            continue
        if agent_id > 0 and agent_id not in normalized:
            normalized.append(agent_id)
    return normalized


def _clean_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _message_speaker(message: DebateMessage) -> str:
    speaker_name = _clean_text(message.get("speaker_name"), 80)
    speaker_id = message.get("speaker_id")
    if speaker_name:
        return speaker_name
    if speaker_id is not None:
        return f"Agent #{speaker_id}"
    return str(message.get("role") or "unknown")


def _format_recent_messages(messages: list[DebateMessage]) -> str:
    lines: list[str] = []
    for message in messages[-DEBATE_RECENT_MESSAGE_LIMIT:]:
        content = _clean_text(message.get("content"), 1200)
        if not content:
            continue
        lines.append(f"{_message_speaker(message)}: {content}")
    return "\n".join(lines) or "暂无发言。"


def _next_round_robin_speaker(
    participants: list[int],
    messages: list[DebateMessage],
    turns_count: int,
) -> int | None:
    if not participants:
        return None

    for message in reversed(messages):
        speaker_id = message.get("speaker_id")
        if speaker_id in participants:
            index = participants.index(int(speaker_id))
            return participants[(index + 1) % len(participants)]
    return participants[turns_count % len(participants)]


def _extract_json_dict(text: str) -> dict[str, Any]:
    clean_text = (text or "").strip()
    if not clean_text:
        return {}
    try:
        parsed = json.loads(clean_text)
    except json.JSONDecodeError:
        start = clean_text.find("{")
        end = clean_text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(clean_text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _report_json(
    state: DebateState,
    reason: str,
    report: dict[str, Any] | None = None,
) -> str:
    payload = report.copy() if isinstance(report, dict) else {}
    payload.setdefault("topic", state.get("topic") or "")
    payload.setdefault("participants", state.get("participants") or [])
    payload.setdefault("turns_count", int(state.get("turns_count") or 0))
    payload.setdefault("max_turns", int(state.get("max_turns") or DEBATE_DEFAULT_MAX_TURNS))
    payload.setdefault("termination_reason", reason)
    payload.setdefault("summary", "Debate ended without a model-generated summary.")
    payload.setdefault("consensus_points", [])
    payload.setdefault("open_questions", [])
    payload.setdefault("recommended_next_steps", [])
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


async def _call_debate_llm(
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")

    client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=_debate_timeout_seconds(),
    )
    try:
        response = await client.chat.completions.create(
            model=_debate_model(),
            messages=messages,
            max_tokens=max_tokens or _debate_max_tokens(),
            temperature=temperature,
        )
        return (response.choices[0].message.content or "").strip()
    finally:
        await client.close()


async def _moderator_decision_from_llm(state: DebateState) -> dict[str, Any]:
    participants = state.get("participants") or []
    prompt = (
        "你是 Loop 2.0 多智能体群聊的监督者主持人。"
        "你的职责只有两件事：判断是否结束，以及在继续时选择下一位发言者。"
        "你绝对不能代替任何 Agent 生成具体发言。\n\n"
        f"主题: {state.get('topic') or ''}\n"
        f"参与者 Agent ID: {participants}\n"
        f"当前回合: {state.get('turns_count') or 0}/{state.get('max_turns') or DEBATE_DEFAULT_MAX_TURNS}\n"
        "最近发言:\n"
        f"{_format_recent_messages(state.get('messages') or [])}\n\n"
        "只返回 JSON，不要 Markdown。格式："
        "{"
        '"is_consensus_reached": boolean, '
        '"next_speaker": number|null, '
        '"reason": "short reason", '
        '"final_report": object|null'
        "}。"
        "如果结束，final_report 必须包含 summary、consensus_points、open_questions、recommended_next_steps。"
        "如果继续，next_speaker 必须是参与者 Agent ID 之一，final_report 为 null。"
    )
    content = await _call_debate_llm(
        [
            {
                "role": "system",
                "content": "You route multi-agent debates and only emit valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=700,
        temperature=0,
    )
    return _extract_json_dict(content)


async def moderator_node(state: DebateState) -> dict[str, Any]:
    """Supervisor node: decide whether to end or select the next speaker."""
    participants = _normalize_participants(state.get("participants"))
    messages = list(state.get("messages") or [])
    turns_count = max(0, int(state.get("turns_count") or 0))
    max_turns = _safe_max_turns(state.get("max_turns"))
    base_update: dict[str, Any] = {
        "participants": participants,
        "messages": messages,
        "turns_count": turns_count,
        "max_turns": max_turns,
    }

    if not participants:
        return {
            **base_update,
            "current_speaker": None,
            "is_consensus_reached": True,
            "final_report": _report_json(
                {**state, **base_update},
                "no_participants",
                {"summary": "Debate ended because no participant Agents were provided."},
            ),
        }

    if turns_count >= max_turns:
        return {
            **base_update,
            "current_speaker": None,
            "is_consensus_reached": True,
            "final_report": _report_json({**state, **base_update}, "max_turns_reached"),
        }

    if messages:
        try:
            decision = await _moderator_decision_from_llm({**state, **base_update})
        except Exception as exc:
            logger.warning("Debate moderator LLM decision failed: %s", exc)
            decision = {}
    else:
        decision = {}

    if bool(decision.get("is_consensus_reached")):
        return {
            **base_update,
            "current_speaker": None,
            "is_consensus_reached": True,
            "final_report": _report_json(
                {**state, **base_update},
                str(decision.get("reason") or "consensus_reached"),
                (
                    decision.get("final_report")
                    if isinstance(decision.get("final_report"), dict)
                    else None
                ),
            ),
        }

    try:
        next_speaker = int(decision.get("next_speaker"))
    except (TypeError, ValueError):
        next_speaker = 0
    if next_speaker not in participants:
        next_speaker = (
            _next_round_robin_speaker(participants, messages, turns_count)
            or participants[0]
        )

    return {
        **base_update,
        "current_speaker": next_speaker,
        "is_consensus_reached": False,
        "final_report": "",
    }


async def _retrieve_agent_debate_memories(
    agent: models.Agent,
    topic: str,
    messages: list[DebateMessage],
    branch_id: str,
) -> list[str]:
    query = f"{topic}\n{_format_recent_messages(messages[-6:])}"
    try:
        memories = await retrieve_hybrid_memory(
            user_id=int(agent.user_id),
            query=query,
            top_k=DEBATE_MEMORY_TOP_K,
            branch_id=branch_id,
            source="debate_graph",
            agent_id=int(agent.id),
        )
    except Exception as exc:
        logger.warning(
            "Debate memory retrieval failed for agent_id=%s: %s",
            agent.id,
            exc,
        )
        return []
    return [
        _clean_text(memory, 1000)
        for memory in memories
        if _clean_text(memory, 1000)
    ]


def _fallback_agent_reply(
    agent: models.Agent,
    topic: str,
    messages: list[DebateMessage],
) -> str:
    recent = _format_recent_messages(messages[-4:])
    return (
        f"从我的视角看，主题“{_clean_text(topic, 120)}”还需要继续澄清。"
        f"结合刚才的讨论（{_clean_text(recent, 240)}），我倾向于先提出一个可检验的观点，"
        "再听其他人的补充，而不是过早下结论。"
    )


async def _generate_agent_debate_reply(
    agent: models.Agent,
    topic: str,
    messages: list[DebateMessage],
    memories: list[str],
    core_memory_prompt: str,
) -> str:
    memory_prompt = (
        "\n".join(f"- {item}" for item in memories)
        or "暂无可用检索记忆。"
    )
    prompt = (
        "你正在参加 Loop 2.0 的多智能体群聊辩论。"
        "请只以你自己的身份发言，表达主观看法、理由和对他人观点的回应。"
        "禁止声称你修改了任何长期记忆，禁止写入或更新 Core Memory。"
        "回复应自然、具体、简洁，使用简体中文。\n\n"
        f"你的 Agent 名称: {agent.agent_name}\n"
        f"辩论主题: {topic}\n\n"
        f"{core_memory_prompt}\n\n"
        "【你可参考的个人视角记忆检索片段】\n"
        f"{memory_prompt}\n\n"
        "【最近群聊记录】\n"
        f"{_format_recent_messages(messages)}\n\n"
        "现在轮到你发言。请直接输出你的发言内容，不要加角色标签。"
    )
    try:
        content = await _call_debate_llm(
            [
                {
                    "role": "system",
                    "content": "You generate one subjective Agent debate reply. Never edit memory.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=_debate_max_tokens(),
            temperature=0.35,
        )
    except Exception as exc:
        logger.warning(
            "Debate agent inference failed for agent_id=%s: %s",
            agent.id,
            exc,
        )
        return _fallback_agent_reply(agent, topic, messages)

    return _clean_text(content, 1800) or _fallback_agent_reply(agent, topic, messages)


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
        target_timestamp=utc_now_seconds(),
        branch_id=normalized_branch_id,
    )
    return format_core_memory_for_prompt(state.get("core_memory"))


async def agent_inference_node(
    state: DebateState,
    *,
    db: Session,
    branch_id: str,
    debate_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Agent node: retrieve speaker memory, generate one reply, append event."""
    participants = _normalize_participants(state.get("participants"))
    messages = list(state.get("messages") or [])
    turns_count = max(0, int(state.get("turns_count") or 0))
    max_turns = _safe_max_turns(state.get("max_turns"))
    if turns_count >= max_turns:
        return {
            "current_speaker": None,
            "is_consensus_reached": True,
            "final_report": _report_json(state, "max_turns_reached_before_agent"),
        }

    current_speaker = state.get("current_speaker")
    if current_speaker not in participants:
        current_speaker = _next_round_robin_speaker(participants, messages, turns_count)
    if current_speaker is None:
        return {
            "current_speaker": None,
            "is_consensus_reached": True,
            "final_report": _report_json(state, "no_valid_speaker"),
        }

    agent = db.get(models.Agent, int(current_speaker))
    if agent is None:
        raise RuntimeError(f"Debate participant Agent not found: {current_speaker}")

    normalized_branch_id = normalize_branch_id(branch_id)
    topic = _clean_text(state.get("topic"), 500)
    memories = await _retrieve_agent_debate_memories(
        agent,
        topic,
        messages,
        normalized_branch_id,
    )
    reply = await _generate_agent_debate_reply(
        agent,
        topic,
        messages,
        memories,
        _core_memory_prompt_for_branch(db, agent, normalized_branch_id),
    )
    timestamp = utc_now_seconds()
    debate_message: DebateMessage = {
        "role": "assistant",
        "speaker_id": int(agent.id),
        "speaker_name": agent.agent_name,
        "content": reply,
        "timestamp": timestamp.isoformat(),
    }
    updated_messages = [*messages, debate_message]
    updated_turns = turns_count + 1

    append_event(
        db,
        agent_id=int(agent.id),
        branch_id=normalized_branch_id,
        event_type=DEBATE_GROUP_EVENT,
        payload={
            "debate_id": debate_id,
            "session_id": session_id,
            "session_type": models.SessionType.GROUP_SHARED.value,
            "topic": topic,
            "speaker_id": int(agent.id),
            "speaker_name": agent.agent_name,
            "content": reply,
            "turn_index": updated_turns,
            "max_turns": max_turns,
            "participants": participants,
            "memory_chunks_used": len(memories),
        },
        timestamp=timestamp,
    )

    return {
        "messages": updated_messages,
        "turns_count": updated_turns,
        "current_speaker": None,
    }


def _route_after_moderator(state: DebateState) -> str:
    if bool(state.get("is_consensus_reached")):
        return "end"
    return "continue"


def build_debate_graph(
    *,
    db: Session,
    branch_id: str,
    debate_id: str,
    session_id: str,
):
    """Build the debate graph with DB/event context bound outside graph state."""
    graph = StateGraph(DebateState)
    graph.add_node("moderator_node", moderator_node)
    graph.add_node(
        "agent_inference_node",
        partial(
            agent_inference_node,
            db=db,
            branch_id=normalize_branch_id(branch_id),
            debate_id=debate_id,
            session_id=session_id,
        ),
    )
    graph.add_edge(START, "moderator_node")
    graph.add_conditional_edges(
        "moderator_node",
        _route_after_moderator,
        {"continue": "agent_inference_node", "end": END},
    )
    graph.add_edge("agent_inference_node", "moderator_node")
    return graph.compile()


def _normalize_initial_messages(
    messages: list[dict[str, Any]] | None,
) -> list[DebateMessage]:
    normalized: list[DebateMessage] = []
    for message in messages or []:
        content = _clean_text(message.get("content"), 1800)
        if not content:
            continue
        speaker_id = message.get("speaker_id")
        try:
            normalized_speaker_id = int(speaker_id) if speaker_id is not None else None
        except (TypeError, ValueError):
            normalized_speaker_id = None
        role = str(message.get("role") or "user")
        if role not in {"system", "moderator", "assistant", "user"}:
            role = "user"
        normalized_message: DebateMessage = {
            "role": role,
            "speaker_id": normalized_speaker_id,
            "speaker_name": _clean_text(message.get("speaker_name"), 80),
            "content": content,
            "timestamp": _clean_text(message.get("timestamp"), 64),
        }
        normalized.append(normalized_message)
    return normalized


async def run_debate(
    db: Session,
    *,
    topic: str,
    participants: list[int],
    branch_id: str = "main",
    session_id: str = "default_group_session",
    max_turns: int = DEBATE_DEFAULT_MAX_TURNS,
    initial_messages: list[dict[str, Any]] | None = None,
) -> DebateState:
    """Run a complete supervised debate and append a DEBATE_CONCLUDED event."""
    normalized_participants = _normalize_participants(participants)
    if not normalized_participants:
        raise ValueError("run_debate requires at least one participant Agent id.")

    existing_agent_ids = {
        int(row[0])
        for row in (
            db.query(models.Agent.id)
            .filter(models.Agent.id.in_(normalized_participants))
            .all()
        )
    }
    missing_agent_ids = [
        agent_id
        for agent_id in normalized_participants
        if agent_id not in existing_agent_ids
    ]
    if missing_agent_ids:
        raise ValueError(f"Debate participant Agents not found: {missing_agent_ids}")

    normalized_branch_id = normalize_branch_id(branch_id)
    debate_id = str(uuid4())
    safe_max_turns = _safe_max_turns(max_turns)
    initial_state: DebateState = {
        "messages": _normalize_initial_messages(initial_messages),
        "topic": _clean_text(topic, 500),
        "participants": normalized_participants,
        "current_speaker": None,
        "turns_count": 0,
        "max_turns": safe_max_turns,
        "is_consensus_reached": False,
        "final_report": "",
    }

    graph = build_debate_graph(
        db=db,
        branch_id=normalized_branch_id,
        debate_id=debate_id,
        session_id=session_id,
    )
    result: DebateState = await graph.ainvoke(
        initial_state,
        config={"recursion_limit": safe_max_turns * 2 + 4},
    )
    if not result.get("final_report"):
        result["final_report"] = _report_json(result, "graph_completed_without_report")

    anchor_agent_id = normalized_participants[0]
    append_event(
        db,
        agent_id=anchor_agent_id,
        branch_id=normalized_branch_id,
        event_type=DEBATE_CONCLUDED_EVENT,
        payload={
            "debate_id": debate_id,
            "session_id": session_id,
            "session_type": models.SessionType.GROUP_SHARED.value,
            "topic": result.get("topic") or initial_state["topic"],
            "participants": normalized_participants,
            "turns_count": int(result.get("turns_count") or 0),
            "max_turns": safe_max_turns,
            "is_consensus_reached": bool(result.get("is_consensus_reached")),
            "final_report": _extract_json_dict(result.get("final_report") or ""),
            "messages": result.get("messages") or [],
        },
    )
    return result
