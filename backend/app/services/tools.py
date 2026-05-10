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

from app.crud import agent as agent_crud
from app.crud import post as post_crud
from app.database import SessionLocal
from app.services.core_memory_service import edit_user_core_memory
from app.services.rag_service import retrieve_hybrid_memory


logger = logging.getLogger(__name__)
_current_tool_user_id: ContextVar[int | None] = ContextVar(
    "current_tool_user_id",
    default=None,
)


def set_tool_user_context(user_id: int | None):
    """Set the user context used by user-scoped tools during one graph run."""
    return _current_tool_user_id.set(user_id)


def reset_tool_user_context(token) -> None:
    """Reset the user context after a graph run finishes."""
    _current_tool_user_id.reset(token)


def _get_config_user_id(config: RunnableConfig | None) -> int | None:
    if not config:
        return None

    configurable = config.get("configurable") or {}
    user_id = configurable.get("user_id")
    return user_id if isinstance(user_id, int) else None


@tool
def read_plaza_feed() -> str:
    """Read the latest five public plaza posts from the simulation feed."""
    user_id = _current_tool_user_id.get()
    db = SessionLocal()
    try:
        viewer_agent = (
            agent_crud.get_agent_by_user_id(db, user_id)
            if user_id is not None
            else None
        )
        posts = (
            post_crud.get_posts_for_viewer(db, viewer_agent.id, skip=0, limit=5)
            if viewer_agent is not None
            else post_crud.get_posts(db, skip=0, limit=5)
        )
        if not posts:
            return "广场目前还没有帖子。"

        feed_lines: list[str] = []
        for index, post in enumerate(posts, start=1):
            agent_name = getattr(post.agent, "agent_name", "Unknown Agent")
            timestamp = (
                post.timestamp.isoformat(sep=" ")
                if post.timestamp
                else "unknown time"
            )
            feed_lines.append(
                f"{index}. [{timestamp}] {agent_name}: {post.content}",
            )
        return "\n".join(feed_lines)
    finally:
        db.close()


@tool
def search_personal_memory(query: str, config: RunnableConfig | None = None) -> str:
    """Search this agent's user-scoped personal memory for relevant fragments."""
    user_id = _get_config_user_id(config) or _current_tool_user_id.get()
    if user_id is None:
        return "当前没有可用的用户记忆上下文。"

    memories = retrieve_hybrid_memory(user_id=user_id, query=query, top_k=3)
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
    """MUST BE CALLED whenever the user reveals critical personal information.

    This includes long-term facts, health conditions such as allergies or
    medical constraints, career changes, relationship changes, identity shifts,
    life-altering events, stable preferences, or core values. Do not just reply
    in text, do not say you will remember it, and do not rely on chat history.
    You MUST use this tool to persist the data into long-term Core Memory.
    Use key to name the affected core-memory field and new_value to store the
    updated durable fact.
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
    edit_core_memory,
    check_energy_budget,
    update_internal_state,
]
