"""LangChain tools that let Loop agents sense and act in the simulation."""

import logging
from contextvars import ContextVar
from datetime import datetime
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from app import models
from app.database import SessionLocal
from app.services.branching import (
    branch_window_filter,
    get_branch_read_windows,
    normalize_branch_id,
)
from app.services.core_memory_service import edit_user_core_memory
from app.services.rag_service import retrieve_hybrid_memory


logger = logging.getLogger(__name__)
_current_tool_user_id: ContextVar[int | None] = ContextVar(
    "current_tool_user_id",
    default=None,
)
_current_tool_agent_id: ContextVar[int | None] = ContextVar(
    "current_tool_agent_id",
    default=None,
)
_current_tool_branch_id: ContextVar[str] = ContextVar(
    "current_tool_branch_id",
    default="main",
)


def set_tool_user_context(
    user_id: int | None,
    agent_id: int | None = None,
    branch_id: str = "main",
):
    """Set the user context used by user-scoped tools during one graph run."""
    user_token = _current_tool_user_id.set(user_id)
    agent_token = _current_tool_agent_id.set(agent_id)
    branch_token = _current_tool_branch_id.set(normalize_branch_id(branch_id))
    return user_token, agent_token, branch_token


def reset_tool_user_context(token) -> None:
    """Reset the user context after a graph run finishes."""
    user_token, agent_token, branch_token = token
    _current_tool_user_id.reset(user_token)
    _current_tool_agent_id.reset(agent_token)
    _current_tool_branch_id.reset(branch_token)


def _get_config_user_id(config: RunnableConfig | None) -> int | None:
    if not config:
        return None

    configurable = config.get("configurable") or {}
    user_id = configurable.get("user_id")
    return user_id if isinstance(user_id, int) else None


def _get_config_agent_id(config: RunnableConfig | None) -> int | None:
    if not config:
        return None

    configurable = config.get("configurable") or {}
    agent_id = configurable.get("agent_id")
    return agent_id if isinstance(agent_id, int) else None


def _get_config_branch_id(config: RunnableConfig | None) -> str | None:
    if not config:
        return None

    configurable = config.get("configurable") or {}
    branch_id = configurable.get("branch_id")
    return normalize_branch_id(branch_id) if isinstance(branch_id, str) else None


@tool
def read_plaza_feed() -> str:
    """Read the latest five public plaza posts from the simulation feed."""
    branch_id = normalize_branch_id(_current_tool_branch_id.get())
    db = SessionLocal()
    try:
        events = (
            db.query(models.EventLog)
            .filter(
                models.EventLog.event_type == "POST_CREATED",
                branch_window_filter(
                    models.EventLog.branch_id,
                    models.EventLog.timestamp,
                    models.EventLog.event_id,
                    get_branch_read_windows(db, branch_id),
                ),
            )
            .order_by(
                models.EventLog.timestamp.desc(),
                models.EventLog.event_id.desc(),
            )
            .limit(5)
            .all()
        )
        if not events:
            return "广场目前还没有帖子。"

        feed_lines: list[str] = []
        for index, event in enumerate(events, start=1):
            payload = event.payload if isinstance(event.payload, dict) else {}
            agent_name = getattr(event.agent, "agent_name", "Unknown Agent")
            timestamp = (
                event.timestamp.isoformat(sep=" ")
                if event.timestamp
                else "unknown time"
            )
            content = str(payload.get("content") or "").strip()
            if not content:
                post_id = payload.get("post_id")
                post = db.get(models.Post, post_id) if isinstance(post_id, int) else None
                content = str(getattr(post, "content", "") or "").strip()
            if not content:
                continue
            feed_lines.append(
                f"{index}. [{timestamp}] {agent_name}: {content}",
            )
        return "\n".join(feed_lines) or "广场目前还没有帖子。"
    finally:
        db.close()


@tool
async def search_personal_memory(
    query: str,
    config: RunnableConfig | None = None,
) -> str:
    """Search this agent's user-scoped personal memory for relevant fragments."""
    user_id = _get_config_user_id(config) or _current_tool_user_id.get()
    agent_id = _get_config_agent_id(config) or _current_tool_agent_id.get()
    branch_id = _get_config_branch_id(config) or _current_tool_branch_id.get()
    if user_id is None:
        return "当前没有可用的用户记忆上下文。"

    memories = await retrieve_hybrid_memory(
        user_id=user_id,
        query=query,
        top_k=3,
        agent_id=agent_id,
        branch_id=branch_id,
    )
    if not memories:
        return "没有检索到相关的个人记忆。"

    return "\n".join(
        f"{index}. {memory}"
        for index, memory in enumerate(memories, start=1)
    )


@tool
def get_current_time() -> str:
    """Get the current local date and time with timezone information."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


@tool
def edit_core_memory(
    key: str,
    new_value: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Persist durable identity facts into Core Memory.

    Kept for internal compatibility. It is intentionally not exposed through
    AGENT_TOOLS; chat-time memory writes are owned by the background watcher.
    """
    user_id = _current_tool_user_id.get()
    if user_id is None:
        return Command(
            update={
                "active_messages": [
                    ToolMessage(
                        content="当前没有可用的用户身份上下文，无法修改 Core Memory。",
                        tool_call_id=tool_call_id,
                    ),
                ],
            },
        )

    db = SessionLocal()
    try:
        db_agent = agent_crud.get_agent_by_user_id(db, user_id)
        agent_id = db_agent.id if db_agent is not None else user_id
        logger.info(f"[Tool Execution] edit_core_memory called by Agent {agent_id}")
        core_memory = edit_user_core_memory(
            db=db,
            user_id=user_id,
            key=key,
            new_value=new_value,
        )
        logger.info(f"[Core Memory Updated] New core concept saved: {new_value}")
    except ValueError as exc:
        return Command(
            update={
                "active_messages": [
                    ToolMessage(content=str(exc), tool_call_id=tool_call_id),
                ],
            },
        )
    finally:
        db.close()

    return Command(
        update={
            "core_memory": core_memory,
            "active_messages": [
                ToolMessage(
                    content=(
                        "Core Memory 已永久更新。"
                        f"{key}={core_memory.get(key, '')}"
                    ),
                    tool_call_id=tool_call_id,
                ),
            ],
        },
    )


@tool
def check_energy_budget(state: Annotated[dict, InjectedState]) -> str:
    """Check how much action budget the agent has left today."""
    current_energy = state.get("energy", 100)
    if not isinstance(current_energy, int):
        current_energy = 100
    current_energy = max(0, min(100, current_energy))
    return f"当前剩余精力值：{current_energy}/100。"


@tool
def update_internal_state(
    new_emotion: str,
    energy_cost: int,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Update this agent's emotion and spend energy after an action or reply."""
    emotion = (new_emotion or "平静").strip()[:24] or "平静"
    cost = max(0, min(int(energy_cost), 100))
    current_energy = state.get("energy", 100)
    if not isinstance(current_energy, int):
        current_energy = 100

    next_energy = max(0, min(100, current_energy - cost))
    return Command(
        update={
            "emotion": emotion,
            "energy": next_energy,
            "active_messages": [
                ToolMessage(
                    content=(
                        f"内部状态已更新：情绪={emotion}，"
                        f"精力={next_energy}/100，本次消耗={cost}。"
                    ),
                    tool_call_id=tool_call_id,
                ),
            ],
        },
    )


AGENT_TOOLS = [
    read_plaza_feed,
    search_personal_memory,
    get_current_time,
    check_energy_budget,
    update_internal_state,
]
