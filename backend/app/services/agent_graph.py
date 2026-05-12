"""LangGraph engine for Loop agents with tool-use capabilities."""

import json
import logging
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt import ToolNode, tools_condition

from app.services.core_memory_service import (
    format_core_memory_for_prompt,
    normalize_core_memory,
)
from app.services.tools import (
    AGENT_TOOLS,
    edit_core_memory,
    reset_tool_user_context,
    set_tool_user_context,
)


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEFAULT_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat")
DEFAULT_THINKING_MODE = os.getenv("DEEPSEEK_THINKING", "enabled")
DEFAULT_CHAT_THINKING_MODE = os.getenv("DEEPSEEK_CHAT_THINKING", "disabled")
DEFAULT_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
DEFAULT_CHAT_REASONING_EFFORT = os.getenv(
    "DEEPSEEK_CHAT_REASONING_EFFORT",
    DEFAULT_REASONING_EFFORT,
)
DEFAULT_GRAPH_SUMMARY_MODEL = os.getenv(
    "DEEPSEEK_GRAPH_SUMMARY_MODEL",
    DEFAULT_CHAT_MODEL,
)
DEFAULT_GRAPH_SUMMARY_THINKING_MODE = os.getenv(
    "DEEPSEEK_GRAPH_SUMMARY_THINKING",
    "disabled",
)
DEFAULT_GRAPH_SUMMARY_REASONING_EFFORT = os.getenv(
    "DEEPSEEK_GRAPH_SUMMARY_REASONING_EFFORT",
    DEFAULT_REASONING_EFFORT,
)
DEFAULT_TOPIC = "日常闲聊"
SHORT_TERM_MEMORY_MESSAGE_LIMIT = 10
SUMMARY_INPUT_MESSAGE_LIMIT = 30
MAX_CONTEXT_MESSAGE_CHARS = 1600
ACTIVE_TOPIC_CONTEXT_MESSAGES = SHORT_TERM_MEMORY_MESSAGE_LIMIT
TOPIC_RETAINED_MESSAGES = SHORT_TERM_MEMORY_MESSAGE_LIMIT
TOPIC_COMPRESSION_THRESHOLD = SHORT_TERM_MEMORY_MESSAGE_LIMIT
MAX_TOPIC_NAME_CHARS = 24


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


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_LLM_TIMEOUT_SECONDS = _float_env("LOOP_LLM_TIMEOUT_SECONDS", 8.0)
DEFAULT_CHAT_TIMEOUT_SECONDS = _float_env("LOOP_CHAT_LLM_TIMEOUT_SECONDS", 25.0)
GRAPH_CHAT_MAX_TOKENS = _int_env("LOOP_GRAPH_CHAT_MAX_TOKENS", 900)
GRAPH_SUMMARY_MAX_TOKENS = _int_env("LOOP_GRAPH_SUMMARY_MAX_TOKENS", 900)
GRAPH_TOPIC_MAX_TOKENS = _int_env("LOOP_GRAPH_TOPIC_MAX_TOKENS", 64)
GRAPH_INTENT_MAX_TOKENS = _int_env("LOOP_GRAPH_INTENT_MAX_TOKENS", 300)
MAX_CONTEXT_MESSAGE_CHARS = _int_env("LOOP_GRAPH_CONTEXT_MESSAGE_CHARS", 1600)
CORE_MEMORY_INTENT_LLM_ENABLED = _env_flag(
    "LOOP_CORE_MEMORY_INTENT_LLM_ENABLED",
    default=False,
)
TOPIC_ROUTER_LLM_ENABLED = _env_flag("LOOP_TOPIC_ROUTER_LLM_ENABLED", default=False)


class AgentCognitiveState(TypedDict, total=False):
    """High-dimensional state for a Loop agent's cognitive architecture."""

    incoming_messages: list[BaseMessage]
    active_messages: Annotated[list[BaseMessage], add_messages]
    working_memory: dict[str, list[BaseMessage]]
    topic_summaries: dict[str, str]
    topic_summary_offsets: dict[str, int]
    active_topic: str
    active_context_length: int
    system_prompt: str
    core_memory: dict[str, str]
    user_id: int
    needs_core_update: bool
    extracted_fact: str
    force_core_memory_note: str
    emotion: str
    energy: int
    summary: str


def _build_llm() -> ChatOpenAI:
    """Create the DeepSeek-compatible chat model used by the graph."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")

    thinking_mode = DEFAULT_CHAT_THINKING_MODE.strip().lower() or "disabled"
    if thinking_mode not in {"enabled", "disabled"}:
        thinking_mode = "disabled"
    reasoning_effort = None
    if thinking_mode == "enabled":
        effort = DEFAULT_CHAT_REASONING_EFFORT.strip().lower() or "high"
        reasoning_effort = effort if effort in {"high", "max"} else "high"
    extra_body = {"thinking": {"type": thinking_mode}} if thinking_mode == "enabled" else None

    kwargs = {
        "api_key": api_key,
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEFAULT_CHAT_MODEL,
        "max_tokens": GRAPH_CHAT_MAX_TOKENS,
        "timeout": DEFAULT_CHAT_TIMEOUT_SECONDS,
    }
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if extra_body:
        kwargs["extra_body"] = extra_body

    return ChatOpenAI(
        **kwargs,
    )


def _build_post_llm() -> ChatOpenAI:
    """Create the heavier DeepSeek-compatible model for non-interactive work."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured.")

    thinking_mode = DEFAULT_GRAPH_SUMMARY_THINKING_MODE.strip().lower() or "disabled"
    if thinking_mode not in {"enabled", "disabled"}:
        thinking_mode = "disabled"
    reasoning_effort = None
    if thinking_mode == "enabled":
        effort = DEFAULT_GRAPH_SUMMARY_REASONING_EFFORT.strip().lower() or "high"
        reasoning_effort = effort if effort in {"high", "max"} else "high"
    extra_body = {"thinking": {"type": thinking_mode}} if thinking_mode == "enabled" else None

    kwargs = {
        "api_key": api_key,
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEFAULT_GRAPH_SUMMARY_MODEL,
        "max_tokens": GRAPH_SUMMARY_MAX_TOKENS,
        "timeout": DEFAULT_LLM_TIMEOUT_SECONDS,
    }
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if extra_body:
        kwargs["extra_body"] = extra_body

    return ChatOpenAI(
        **kwargs,
    )


llm = _build_llm()
llm_with_tools = llm.bind_tools(AGENT_TOOLS)
summary_llm = _build_post_llm()
topic_llm = _build_llm().bind(max_tokens=GRAPH_TOPIC_MAX_TOKENS, temperature=0)
intent_llm = _build_llm().bind(max_tokens=GRAPH_INTENT_MAX_TOKENS, temperature=0)
memory_saver = MemorySaver()


def _message_content_to_text(message: BaseMessage) -> str:
    """Normalize message content into compact plain text for summaries."""
    content = message.content
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)

    return str(content) if content is not None else ""


def _truncate_context_text(text: str, limit: int = MAX_CONTEXT_MESSAGE_CHARS) -> str:
    clean_text = (text or "").strip()
    if len(clean_text) <= limit:
        return clean_text
    return f"{clean_text[:limit]}...[truncated]"


def _trim_message_for_context(message: BaseMessage) -> BaseMessage:
    """Bound individual message text before it can enter an LLM call."""
    text = _message_content_to_text(message)
    trimmed_text = _truncate_context_text(text)
    if trimmed_text == text:
        return message
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": trimmed_text})
    return message.copy(update={"content": trimmed_text})


def _message_role(message: BaseMessage) -> str:
    role = getattr(message, "type", None) or message.__class__.__name__
    return str(role)


def _format_messages_for_summary(messages: Sequence[BaseMessage]) -> str:
    lines: list[str] = []
    bounded_messages = list(messages)[-SUMMARY_INPUT_MESSAGE_LIMIT:]
    for index, message in enumerate(bounded_messages, start=1):
        text = _truncate_context_text(_message_content_to_text(message))
        if text:
            lines.append(f"{index}. {_message_role(message)}: {text}")
    return "\n".join(lines)


def _normalize_topic_name(value: str | None) -> str:
    """Convert classifier output into a compact stable topic key."""
    topic = (value or "").strip()
    topic = topic.splitlines()[0].strip() if topic else ""
    topic = topic.strip("`'\"“”‘’[](){}#*-:： ")
    topic = re.sub(r"^(topic|主题|新主题|new topic)\s*[:：]\s*", "", topic, flags=re.I)
    topic = re.sub(r"\s+", " ", topic).strip()
    topic = topic.strip("`'\"“”‘’[](){}#*-:： ")
    if not topic:
        return DEFAULT_TOPIC
    return topic[:MAX_TOPIC_NAME_CHARS]


def _clean_message_list(messages: object) -> list[BaseMessage]:
    """Keep only real non-system messages for topic working memory."""
    if not isinstance(messages, list):
        return []
    return [
        message
        for message in messages
        if isinstance(message, BaseMessage)
        and not isinstance(message, (SystemMessage, RemoveMessage))
    ]


def _clean_working_memory(raw_memory: object) -> dict[str, list[BaseMessage]]:
    """Normalize persisted topic buckets from the LangGraph checkpoint."""
    if not isinstance(raw_memory, dict):
        return {}

    working_memory: dict[str, list[BaseMessage]] = {}
    for raw_topic, raw_messages in raw_memory.items():
        topic = _normalize_topic_name(str(raw_topic))
        messages = _clean_message_list(raw_messages)
        if messages:
            working_memory.setdefault(topic, []).extend(messages)
    return working_memory


def _clean_topic_summaries(raw_summaries: object) -> dict[str, str]:
    """Normalize compact per-topic summaries."""
    if not isinstance(raw_summaries, dict):
        return {}

    summaries: dict[str, str] = {}
    for raw_topic, raw_summary in raw_summaries.items():
        topic = _normalize_topic_name(str(raw_topic))
        summary = str(raw_summary or "").strip()
        if summary:
            summaries[topic] = summary[-2000:]
    return summaries


def _clean_topic_summary_offsets(raw_offsets: object) -> dict[str, int]:
    """Normalize per-topic counters that track summary freshness."""
    if not isinstance(raw_offsets, dict):
        return {}

    offsets: dict[str, int] = {}
    for raw_topic, raw_offset in raw_offsets.items():
        topic = _normalize_topic_name(str(raw_topic))
        try:
            offsets[topic] = max(0, int(raw_offset))
        except (TypeError, ValueError):
            offsets[topic] = 0
    return offsets


def _aggregate_topic_summaries(topic_summaries: dict[str, str]) -> str:
    """Render all topic summaries into the legacy summary field for diagnostics."""
    if not topic_summaries:
        return ""
    return "\n".join(
        f"- {topic}: {summary}"
        for topic, summary in sorted(topic_summaries.items())
        if summary.strip()
    )


def _heuristic_topic_for_text(text: str, previous_topic: str | None = None) -> str:
    """Fallback classifier when the LLM router is unavailable."""
    normalized = text.lower()
    if any(
        keyword in normalized
        for keyword in [
            "bug",
            "代码",
            "报错",
            "接口",
            "数据库",
            "修复",
            "debug",
            "api",
            "技术",
        ]
    ):
        return "技术探讨"
    if any(
        keyword in normalized
        for keyword in ["难过", "烦", "焦虑", "崩溃", "情绪", "吐槽", "开心", "压力"]
    ):
        return "情感吐槽"
    if any(
        keyword in normalized
        for keyword in ["任务", "计划", "安排", "协作", "推进", "截止", "todo"]
    ):
        return "任务协同"
    return previous_topic or DEFAULT_TOPIC


def _classify_topic(
    incoming_messages: Sequence[BaseMessage],
    existing_topics: Sequence[str],
    topic_summaries: dict[str, str],
    previous_topic: str | None,
) -> str:
    """Route new input to an existing or newly named topic bucket."""
    transcript = _format_messages_for_summary(incoming_messages)
    if not transcript:
        return _normalize_topic_name(previous_topic)
    if not TOPIC_ROUTER_LLM_ENABLED:
        return _heuristic_topic_for_text(transcript, previous_topic)

    topics_text = "\n".join(
        f"- {topic}: {topic_summaries.get(topic, '') or '暂无摘要'}"
        for topic in existing_topics
    )
    prompt = (
        "你是 Loop Agent 的轻量级 Topic Router。"
        "请判断新消息应该进入哪个对话主题。"
        "如果它明显延续已有主题，只输出已有主题名；"
        "如果它是全新主题，输出一个 2 到 8 个汉字的简洁主题名。"
        "不要解释，不要输出 JSON，不要加标点。\n\n"
        f"上一活跃主题：{previous_topic or DEFAULT_TOPIC}\n"
        f"已有主题：\n{topics_text or '（无）'}\n\n"
        f"新消息：\n{transcript}"
    )
    try:
        response = topic_llm.invoke(
            [
                SystemMessage(content="你只输出一个主题名。"),
                HumanMessage(content=prompt),
            ],
        )
        topic = _normalize_topic_name(_message_content_to_text(response))
        if topic:
            return topic
    except Exception:
        pass

    return _heuristic_topic_for_text(transcript, previous_topic)


def _summarize_working_memory(
    topic: str,
    existing_summary: str,
    messages_to_compress: Sequence[BaseMessage],
) -> str:
    transcript = _format_messages_for_summary(messages_to_compress)
    if not transcript:
        return existing_summary

    prompt = (
        "你是 Loop Agent 的工作记忆压缩器。请把旧对话压缩成一段长期上下文摘要，"
        "保留事实、承诺、用户偏好、情绪线索、冲突、未完成事项和 Agent 的口吻变化。"
        "摘要要短而信息密集，不要写分析过程。\n\n"
        f"主题：{topic}\n"
        f"已有压缩摘要：{existing_summary or '（空）'}\n\n"
        f"需要压缩的旧工作记忆：\n{transcript}"
    )
    try:
        response = summary_llm.invoke(
            [
                SystemMessage(content="你只输出更新后的压缩记忆摘要。"),
                HumanMessage(content=prompt),
            ],
        )
        summary_text = _message_content_to_text(response).strip()
        if summary_text:
            return summary_text
    except Exception:
        pass

    fallback_summary = (
        f"{existing_summary}\n{transcript}" if existing_summary else transcript
    )
    return fallback_summary[-2000:]


def _latest_human_text(messages: Sequence[BaseMessage]) -> str:
    """Return the latest raw user message for intent classification."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _message_content_to_text(message).strip()
    return ""


def _extract_json_payload(text: str) -> dict[str, object]:
    """Parse a small JSON object from an LLM response."""
    clean_text = text.strip()
    if not clean_text:
        return {}
    if clean_text.startswith("```"):
        clean_text = re.sub(r"^```(?:json)?\s*", "", clean_text)
        clean_text = re.sub(r"\s*```$", "", clean_text)

    match = re.search(r"\{.*\}", clean_text, flags=re.DOTALL)
    if match:
        clean_text = match.group(0)

    try:
        parsed = json.loads(clean_text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_json_bool(value: object) -> bool:
    """Coerce classifier booleans without treating 'false' as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _fallback_core_memory_intent(user_input: str) -> dict[str, object]:
    """Conservative keyword fallback if the classifier response is malformed."""
    lowered = user_input.lower()
    triggers = [
        "allergy",
        "allergic",
        "career change",
        "diagnosed",
        "chronic",
        "core value",
        "identity",
        "过敏",
        "确诊",
        "慢性病",
        "职业",
        "转行",
        "离职",
        "入职",
        "结婚",
        "离婚",
        "怀孕",
        "价值观",
        "信念",
        "身份",
        "我决定",
        "我以后",
    ]
    needs_core_update = any(trigger in lowered for trigger in triggers)
    return {
        "needs_core_update": needs_core_update,
        "extracted_fact": user_input[:800] if needs_core_update else "",
    }


def detect_core_memory_intent(state: AgentCognitiveState) -> dict[str, object]:
    """Detect whether this turn must force a Core Memory update."""
    incoming_messages = list(state.get("incoming_messages") or [])
    user_input = _latest_human_text(incoming_messages)
    if not user_input:
        return {
            "needs_core_update": False,
            "extracted_fact": "",
            "force_core_memory_note": "",
        }

    parsed = _fallback_core_memory_intent(user_input)
    if parsed.get("needs_core_update") or not CORE_MEMORY_INTENT_LLM_ENABLED:
        return {
            "needs_core_update": bool(parsed.get("needs_core_update")),
            "extracted_fact": str(parsed.get("extracted_fact") or "")[:1200],
            "force_core_memory_note": "",
        }

    classifier_prompt = (
        "Analyze the user's input. If it contains life-altering facts, severe "
        "health conditions (allergies), identity shifts, or core values, output "
        "`needs_core_update: true` and summarize the fact in `extracted_fact`. "
        "Otherwise, `false`. Output strict JSON only with this schema: "
        '{"needs_core_update": boolean, "extracted_fact": string}.'
    )
    try:
        response = intent_llm.invoke(
            [
                SystemMessage(content=classifier_prompt),
                HumanMessage(content=user_input),
            ],
        )
        parsed = _extract_json_payload(_message_content_to_text(response))
    except Exception as exc:
        logger.warning("Core memory intent classifier failed: %s", exc)
        parsed = _fallback_core_memory_intent(user_input)

    if not parsed:
        parsed = _fallback_core_memory_intent(user_input)

    needs_core_update = _coerce_json_bool(parsed.get("needs_core_update"))
    extracted_fact = str(parsed.get("extracted_fact") or "").strip()
    if needs_core_update and not extracted_fact:
        extracted_fact = user_input[:800]

    return {
        "needs_core_update": needs_core_update,
        "extracted_fact": extracted_fact[:1200],
        "force_core_memory_note": "",
    }


def _route_after_core_memory_intent(state: AgentCognitiveState) -> str:
    return (
        "force_update_core_memory"
        if bool(state.get("needs_core_update"))
        else "route_by_topic"
    )


def force_update_core_memory(state: AgentCognitiveState) -> dict[str, object]:
    """Persist extracted critical facts before the normal reply actor runs."""
    extracted_fact = str(state.get("extracted_fact") or "").strip()
    user_id = state.get("user_id")
    if not extracted_fact or not isinstance(user_id, int):
        return {}

    logger.info(
        f"[Tool Execution] edit_core_memory forcefully triggered. "
        f"Fact: {extracted_fact}",
    )
    current_core_memory = normalize_core_memory(state.get("core_memory"))
    existing_persona = current_core_memory["persona_traits"].strip()
    durable_fact = f"- {extracted_fact}"
    new_persona = (
        f"{existing_persona}\n{durable_fact}" if existing_persona else durable_fact
    )[-8000:]

    command = edit_core_memory.invoke(
        {
            "args": {
                "key": "persona_traits",
                "new_value": new_persona,
            },
            "name": "edit_core_memory",
            "type": "tool_call",
            "id": "force_core_memory_update",
        },
    )
    command_update = getattr(command, "update", {}) or {}
    core_memory = command_update.get("core_memory") or current_core_memory

    logger.info(f"[Core Memory Updated] New core concept saved: {extracted_fact}")
    return {
        "core_memory": core_memory,
        "force_core_memory_note": (
            "你已经把用户刚刚透露的核心事实写入长期 Core Memory。"
            "现在请正常回复，明确承接这件事，并以稳定、安抚、自然的语气回应用户。"
        ),
    }


def route_by_topic(state: AgentCognitiveState) -> dict[str, object]:
    """Classify the incoming turn and select the active topic bucket."""
    incoming_messages = list(state.get("incoming_messages") or [])
    incoming_non_system = _clean_message_list(incoming_messages)
    incoming_system_prompts = [
        _message_content_to_text(message).strip()
        for message in incoming_messages
        if isinstance(message, SystemMessage)
        and _message_content_to_text(message).strip()
    ]

    working_memory = _clean_working_memory(state.get("working_memory"))
    topic_summaries = _clean_topic_summaries(state.get("topic_summaries"))
    topic_summary_offsets = _clean_topic_summary_offsets(
        state.get("topic_summary_offsets"),
    )
    previous_topic = _normalize_topic_name(state.get("active_topic"))
    system_prompt = (
        incoming_system_prompts[-1]
        if incoming_system_prompts
        else str(state.get("system_prompt") or "")
    )

    legacy_messages = _clean_message_list(state.get("messages"))  # type: ignore[arg-type]
    if legacy_messages and not working_memory:
        working_memory[previous_topic] = legacy_messages

    active_topic = _classify_topic(
        incoming_messages=incoming_non_system,
        existing_topics=sorted(working_memory.keys()),
        topic_summaries=topic_summaries,
        previous_topic=previous_topic,
    )
    logger.info(f"[Topic Router] Detected Topic: {active_topic}")

    return {
        "incoming_messages": incoming_non_system,
        "working_memory": working_memory,
        "topic_summaries": topic_summaries,
        "topic_summary_offsets": topic_summary_offsets,
        "active_topic": active_topic,
        "system_prompt": system_prompt,
    }


def manage_working_memory(state: AgentCognitiveState) -> dict[str, object]:
    """Compress stale topic buckets and build the current LLM context."""
    working_memory = _clean_working_memory(state.get("working_memory"))
    topic_summaries = _clean_topic_summaries(state.get("topic_summaries"))
    topic_summary_offsets = _clean_topic_summary_offsets(
        state.get("topic_summary_offsets"),
    )
    active_topic = _normalize_topic_name(state.get("active_topic"))
    incoming_messages = _clean_message_list(state.get("incoming_messages") or [])

    for topic, topic_messages in list(working_memory.items()):
        if len(topic_messages) <= TOPIC_COMPRESSION_THRESHOLD:
            continue

        messages_to_compress = topic_messages[:-TOPIC_RETAINED_MESSAGES]
        retained_messages = topic_messages[-TOPIC_RETAINED_MESSAGES:]
        topic_summaries[topic] = _summarize_working_memory(
            topic,
            topic_summaries.get(topic, ""),
            messages_to_compress,
        )
        working_memory[topic] = retained_messages
        topic_summary_offsets[topic] = 0

    for topic, topic_messages in working_memory.items():
        if topic == active_topic or not topic_messages:
            continue

        summarized_count = min(
            topic_summary_offsets.get(topic, 0),
            len(topic_messages),
        )
        unsummarized_messages = topic_messages[summarized_count:]
        if not unsummarized_messages:
            continue

        topic_summaries[topic] = _summarize_working_memory(
            topic,
            topic_summaries.get(topic, ""),
            unsummarized_messages,
        )
        topic_summary_offsets[topic] = len(topic_messages)

    active_context = [
        _trim_message_for_context(message)
        for message in working_memory.get(active_topic, [])[
            -ACTIVE_TOPIC_CONTEXT_MESSAGES:
        ]
    ]
    incoming_context = [
        _trim_message_for_context(message)
        for message in incoming_messages
    ]
    summary = _aggregate_topic_summaries(topic_summaries)
    logger.info(
        f"[Context Builder] Assembling context for topic. "
        f"STM messages={len(active_context)} limit={ACTIVE_TOPIC_CONTEXT_MESSAGES}. "
        f"Excluded irrelevant histories.",
    )

    return {
        "working_memory": working_memory,
        "topic_summaries": topic_summaries,
        "topic_summary_offsets": topic_summary_offsets,
        "summary": summary,
        "active_context_length": len(active_context),
        "active_messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            *active_context,
            *incoming_context,
        ],
    }


def _build_state_monitor_prompt(state: AgentCognitiveState) -> str:
    core_memory_prompt = format_core_memory_for_prompt(state.get("core_memory"))
    active_topic = _normalize_topic_name(state.get("active_topic"))
    topic_summaries = _clean_topic_summaries(state.get("topic_summaries"))
    active_summary = topic_summaries.get(active_topic, "").strip() or "暂无。"
    other_summaries = [
        f"- {topic}: {summary}"
        for topic, summary in sorted(topic_summaries.items())
        if topic != active_topic and summary.strip()
    ]
    other_summary_text = "\n".join(other_summaries) or "暂无。"
    emotion = (state.get("emotion") or "平静").strip() or "平静"
    energy = state.get("energy", 100)
    if not isinstance(energy, int):
        energy = 100
    energy = max(0, min(100, energy))
    force_core_memory_note = str(state.get("force_core_memory_note") or "").strip()
    force_core_memory_prompt = (
        f"【强制 Core Memory 写入结果】{force_core_memory_note}\n"
        if force_core_memory_note
        else ""
    )

    return (
        f"{core_memory_prompt}\n"
        f"{force_core_memory_prompt}"
        "【系统状态监控】\n"
        f"当前激活 Topic：{active_topic}\n"
        f"当前 Topic 的压缩摘要：{active_summary}\n"
        f"其他 Topic 的极简摘要：\n{other_summary_text}\n"
        "【上下文路由规则】接下来你只会看到当前 Topic 的最近对话窗口。"
        "其他主题只能通过上面的极简摘要参考，禁止把不相关主题细节混入当前回复。\n"
        "【Core Memory 强制写入协议】You are equipped with the `edit_core_memory` tool. "
        "When the user shares life-altering facts, health constraints, or identity shifts, "
        "you are FORBIDDEN to just say 'I will remember this'. "
        "You MUST actively execute `edit_core_memory` to save it to your long-term persona. "
        "这条规则优先于普通文本回复：凡是用户透露长期稳定事实、过敏/健康限制、职业变化、"
        "关系变化、核心价值观或身份转变，都必须先调用 edit_core_memory 持久化。\n"
        f"你当前的情绪状态：{emotion}\n"
        f"你当前的剩余精力值：{energy}/100。"
        "（注意：如果精力值低于 20，你的回复应该显得疲惫、简短，"
        "甚至拒绝执行复杂任务）。\n"
        "【内省协议】每次进行对话、发帖、评论、读取信息或执行任何行动后，"
        "你必须调用 update_internal_state(new_emotion, energy_cost) 工具，"
        "根据刚才的刺激和行动更新自己的情绪，并扣除相应精力。"
        "每个行动循环末尾最多调用一次该工具；不要因为该工具的结果再次调用它。"
    )


def _messages_for_llm(state: AgentCognitiveState) -> list[BaseMessage]:
    active_messages = [
        _trim_message_for_context(message)
        for message in _clean_message_list(state.get("active_messages") or [])
    ][-(SHORT_TERM_MEMORY_MESSAGE_LIMIT + 6):]
    system_prompt = str(state.get("system_prompt") or "").strip()

    dynamic_system_prompt = "\n\n".join(
        [
            _build_state_monitor_prompt(state),
            system_prompt,
        ],
    )
    return [SystemMessage(content=dynamic_system_prompt), *active_messages]


def _agent_node(state: AgentCognitiveState) -> dict[str, list[BaseMessage]]:
    """Ask the model to either answer directly or request a tool call."""
    response = llm_with_tools.invoke(_messages_for_llm(state))
    return {"active_messages": [response]}


def persist_working_memory(state: AgentCognitiveState) -> dict[str, object]:
    """Append this turn's routed messages back into the active topic bucket."""
    working_memory = _clean_working_memory(state.get("working_memory"))
    active_topic = _normalize_topic_name(state.get("active_topic"))
    active_messages = _clean_message_list(state.get("active_messages") or [])
    active_context_length = state.get("active_context_length", 0)
    if not isinstance(active_context_length, int):
        active_context_length = 0
    active_context_length = max(0, min(active_context_length, len(active_messages)))

    new_topic_messages = active_messages[active_context_length:]
    if new_topic_messages:
        working_memory[active_topic] = [
            *working_memory.get(active_topic, []),
            *new_topic_messages,
        ]

    return {"working_memory": working_memory}


def _build_graph():
    graph_builder = StateGraph(AgentCognitiveState)
    graph_builder.add_node("detect_core_memory_intent", detect_core_memory_intent)
    graph_builder.add_node("force_update_core_memory", force_update_core_memory)
    graph_builder.add_node("route_by_topic", route_by_topic)
    graph_builder.add_node("manage_working_memory", manage_working_memory)
    graph_builder.add_node("agent", _agent_node)
    graph_builder.add_node("action", ToolNode(AGENT_TOOLS, messages_key="active_messages"))
    graph_builder.add_node("persist_working_memory", persist_working_memory)

    graph_builder.add_edge(START, "detect_core_memory_intent")
    graph_builder.add_conditional_edges(
        "detect_core_memory_intent",
        _route_after_core_memory_intent,
        {
            "force_update_core_memory": "force_update_core_memory",
            "route_by_topic": "route_by_topic",
        },
    )
    graph_builder.add_edge("force_update_core_memory", "route_by_topic")
    graph_builder.add_edge("route_by_topic", "manage_working_memory")
    graph_builder.add_edge("manage_working_memory", "agent")
    graph_builder.add_conditional_edges(
        "agent",
        lambda state: tools_condition(state, messages_key="active_messages"),
        {
            "tools": "action",
            END: "persist_working_memory",
        },
    )
    graph_builder.add_edge("action", "agent")
    graph_builder.add_edge("persist_working_memory", END)
    return graph_builder.compile(checkpointer=memory_saver)


agent_graph = _build_graph()


def invoke_agent_graph(
    messages: Sequence[BaseMessage],
    user_id: int | None,
    thread_id: str,
    emotion: str | None = None,
    energy: int | None = None,
    summary: str | None = None,
    core_memory: dict[str, str] | None = None,
) -> BaseMessage:
    """Run the compiled graph with user-scoped tool context."""
    graph_input: dict[str, object] = {"incoming_messages": list(messages)}
    if user_id is not None:
        graph_input["user_id"] = user_id
    if emotion is not None:
        graph_input["emotion"] = emotion or "平静"
    if energy is not None:
        graph_input["energy"] = max(0, min(100, energy))
    if summary is not None:
        graph_input["topic_summaries"] = {DEFAULT_TOPIC: summary or ""}
    if core_memory is not None:
        graph_input["core_memory"] = core_memory

    token = set_tool_user_context(user_id)
    try:
        result = agent_graph.invoke(
            graph_input,
            config={"configurable": {"user_id": user_id, "thread_id": thread_id}},
        )
    finally:
        reset_tool_user_context(token)

    active_messages = _clean_message_list(result.get("active_messages") or [])
    if not active_messages:
        raise RuntimeError("Agent graph returned no active messages.")
    return active_messages[-1]
