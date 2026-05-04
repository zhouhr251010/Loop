"""LLM service for generating agent posts from identity-core data."""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


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

    return (
        "You are a virtual human in a computational social science simulation. "
        f"Your MBTI type is {mbti}. "
        f"Your Big Five scores are {big_five}. "
        f"Your Schwartz values are {schwartz}. "
        "Based on these identity-core traits, write one short everyday social "
        "media post in Simplified Chinese. Keep it within 50 Chinese characters. "
        "Output only the post body. Do not explain the personality dimensions. "
        "Do not use quotation marks."
    )


def _mock_agent_post(user_data: dict[str, Any]) -> str:
    """Return a stable fallback post when no API key is configured."""
    mbti = user_data.get("mbti_type") or "UNKNOWN"
    return f"[Mock] {mbti} agent is reflecting quietly and sharing a small daily thought."


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
