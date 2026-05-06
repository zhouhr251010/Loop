"""LLM service for generating agent posts from identity-core data."""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .rag_service import retrieve_memory


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
PROJECT_ROOT = Path(__file__).resolve().parents[3]

load_dotenv(PROJECT_ROOT / ".env")


def _as_dict(user_data: Any) -> dict[str, Any]:
    """Normalize SQLAlchemy user objects or plain dicts into prompt data."""
    if isinstance(user_data, dict):
        return user_data

    return {
        "username": getattr(user_data, "username", "unknown_user"),
        "mbti_type": getattr(user_data, "mbti_type", None),
        "big_five_scores": getattr(user_data, "big_five_scores", None),
        "schwartz_values": getattr(user_data, "schwartz_values", None),
        "autobiography": getattr(user_data, "autobiography", None),
    }


def _build_identity_prompt(user_data: dict[str, Any]) -> str:
    """Build the identity-core prompt used by the simulation engine."""
    mbti = user_data.get("mbti_type") or "unknown"
    big_five = json.dumps(
        user_data.get("big_five_scores") or {},
        ensure_ascii=False,
        sort_keys=True,
    )
    schwartz = json.dumps(
        user_data.get("schwartz_values") or {},
        ensure_ascii=False,
        sort_keys=True,
    )
    autobiography = (user_data.get("autobiography") or "").strip()

    memory_prompt = ""
    if autobiography:
        memory_prompt = (
            "Core memory / autobiography: "
            f"{autobiography}. "
            "Treat this as your life background and emotional foundation. "
        )

    return (
        "You are a virtual human in a computational social science simulation. "
        f"Your MBTI type is {mbti}. "
        f"Your Big Five scores are {big_five}. "
        f"Your Schwartz values are {schwartz}. "
        f"{memory_prompt}"
        "Based on these identity-core traits, write one short everyday social "
        "media post in Simplified Chinese. Keep it within 50 Chinese characters. "
        "Output only the post body. Do not explain the personality dimensions. "
        "Do not use quotation marks."
    )


def _build_chat_system_prompt(
    agent: Any,
    retrieved_memories: list[str] | None = None,
) -> str:
    """Build the private-sync system prompt for a specific agent."""
    user = getattr(agent, "user", None)
    user_data = _as_dict(user)
    identity_prompt = _build_identity_prompt(user_data)
    autobiography = (user_data.get("autobiography") or "").strip()
    memory_instruction = ""
    if autobiography:
        memory_instruction = (
            "The user's autobiography is your core memory. Preserve continuity "
            "with it and respond as the user's emotionally aligned digital twin. "
        )

    rag_context = ""
    if retrieved_memories:
        memory_lines = "\n".join(
            f"{index}. {memory}"
            for index, memory in enumerate(retrieved_memories, start=1)
        )
        rag_context = (
            "以下是该用户记忆金库中检索到的真实过往记忆。你必须用这些记忆来校准"
            "你的世界观、关注点、语气、词汇和情绪温度，让回复明显更像这个用户的"
            "数字孪生。不要说“根据你的记忆”，也不要机械复述原文；要自然吸收它：\n"
            f"{memory_lines}\n"
        )

    return (
        "You are the user's private daily-sync Agent in Loop. "
        "Reply warmly, concretely, and briefly in Simplified Chinese. "
        "Do not claim to be an external assistant; speak as the user's reflective "
        "digital twin. "
        f"{memory_instruction}"
        f"{rag_context}"
        f"Identity context: {identity_prompt}"
    )


def _mock_agent_post(user_data: dict[str, Any]) -> str:
    """Return a stable fallback post when no API key is configured."""
    mbti = user_data.get("mbti_type") or "UNKNOWN"
    return f"[Mock] {mbti} agent is reflecting quietly and sharing a small daily thought."


def _mock_agent_reply(
    agent: Any,
    user_message: str,
    retrieved_memories: list[str] | None = None,
) -> str:
    """Return a safe fallback chat reply when DeepSeek is unavailable."""
    agent_name = getattr(agent, "agent_name", "Agent")
    memory_note = ""
    if retrieved_memories:
        memory_note = f" I found {len(retrieved_memories)} memory fragments for context."
    return (
        f"[Mock] {agent_name}: I heard you say '{user_message[:80]}'. "
        f"Let's keep tracking this feeling in our nightly sync.{memory_note}"
    )


def generate_agent_post(user_data: Any) -> str:
    """Generate a short social post for an agent, falling back safely to mock text."""
    normalized_user = _as_dict(user_data)
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return _mock_agent_post(normalized_user)

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=15.0,
        )
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the simulation engine for the Loop research "
                        "platform. Generate credible, concise, everyday "
                        "Simplified Chinese posts."
                    ),
                },
                {"role": "user", "content": _build_identity_prompt(normalized_user)},
            ],
            max_tokens=80,
        )
        generated_text = (response.choices[0].message.content or "").strip()
        return generated_text[:100] if generated_text else _mock_agent_post(normalized_user)
    except Exception:
        return _mock_agent_post(normalized_user)


def chat_with_agent(agent: Any, user_message: str) -> tuple[str, int]:
    """Generate a private daily-sync reply for a user message."""
    retrieved_memories: list[str] = []
    user = getattr(agent, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is not None:
        try:
            retrieved_memories = retrieve_memory(user_id, user_message, top_k=3)
        except Exception:
            retrieved_memories = []

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return _mock_agent_reply(agent, user_message, retrieved_memories), len(
            retrieved_memories,
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
                    "content": _build_chat_system_prompt(agent, retrieved_memories),
                },
                {"role": "user", "content": user_message},
            ],
            max_tokens=240,
        )
        generated_text = (response.choices[0].message.content or "").strip()
        if generated_text:
            return generated_text, len(retrieved_memories)
        return _mock_agent_reply(agent, user_message, retrieved_memories), len(
            retrieved_memories,
        )
    except Exception:
        return _mock_agent_reply(agent, user_message, retrieved_memories), len(
            retrieved_memories,
        )
