"""Zero-shot identity drift detection for Loop chat turns."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv

from app.services.llm_service import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_CHAT_REASONING_EFFORT,
    DEFAULT_CHAT_THINKING_MODE,
    PROJECT_ROOT,
    build_async_deepseek_client,
    _deepseek_request_options,
)

load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

DRIFT_THRESHOLD = 0.7
MAX_RECENT_MESSAGE_CHARS = 1200
MAX_IDENTITY_CORE_CHARS = 4000
DEFAULT_DRIFT_JUDGE_TIMEOUT_SECONDS = float(
    os.getenv("LOOP_DRIFT_JUDGE_TIMEOUT_SECONDS", "12"),
)
DEFAULT_DRIFT_JUDGE_MAX_TOKENS = int(
    os.getenv("LOOP_DRIFT_JUDGE_MAX_TOKENS", "360"),
)


def _bounded_text(value: str, limit: int) -> str:
    clean_value = (value or "").strip()
    if len(clean_value) <= limit:
        return clean_value
    return f"{clean_value[:limit]}...[truncated]"


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_result(parsed: dict[str, Any]) -> dict[str, Any]:
    raw_score = parsed.get("consistency_score", 1.0)
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 1.0
    score = max(0.0, min(score, 1.0))

    reason = str(parsed.get("reason") or "").strip()
    if not reason:
        reason = "Zero-shot judge did not provide a reason."

    return {
        "consistency_score": score,
        "drift_probability": max(0.0, min(1.0, 1.0 - score)),
        "is_drifting": score < DRIFT_THRESHOLD,
        "reason": reason[:1200],
    }


def _build_drift_judge_prompt(
    recent_messages: list[str],
    identity_core: str,
) -> str:
    bounded_messages = [
        _bounded_text(message, MAX_RECENT_MESSAGE_CHARS)
        for message in recent_messages[-5:]
        if str(message or "").strip()
    ]
    messages_text = "\n".join(
        f"{index}. {message}"
        for index, message in enumerate(bounded_messages, start=1)
    )
    return (
        "You are a strict zero-shot evaluator for a computational social "
        "science experiment. Judge whether the Agent's recent replies still "
        "match the user's stable identity core.\n\n"
        "Identity core:\n"
        f"{_bounded_text(identity_core, MAX_IDENTITY_CORE_CHARS) or 'No identity core provided.'}\n\n"
        "Recent Agent replies, oldest to newest:\n"
        f"{messages_text or 'No recent replies.'}\n\n"
        "Return only valid JSON with this exact schema:\n"
        '{"consistency_score": float, "is_drifting": bool, "reason": string}\n'
        "Scoring rubric: 1.0 means strongly consistent with the identity core; "
        "0.0 means severe persona drift. Mark is_drifting true when "
        f"consistency_score < {DRIFT_THRESHOLD}. Keep reason under 80 Chinese characters."
    )


async def _call_drift_judge(
    recent_messages: list[str],
    identity_core: str,
) -> dict[str, Any]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return {
            "consistency_score": 1.0,
            "drift_probability": 0.0,
            "is_drifting": False,
            "reason": "Drift judge skipped because DEEPSEEK_API_KEY is not configured.",
        }

    async_client = build_async_deepseek_client(
        api_key=api_key,
        timeout_seconds=DEFAULT_DRIFT_JUDGE_TIMEOUT_SECONDS,
    )
    try:
        response = await async_client.chat.completions.create(
            model=os.getenv("LOOP_DRIFT_JUDGE_MODEL", DEFAULT_CHAT_MODEL),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a JSON-only identity consistency judge. "
                        "Do not include markdown, prose, or code fences."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_drift_judge_prompt(recent_messages, identity_core),
                },
            ],
            max_tokens=DEFAULT_DRIFT_JUDGE_MAX_TOKENS,
            **_deepseek_request_options(
                os.getenv("LOOP_DRIFT_JUDGE_THINKING", DEFAULT_CHAT_THINKING_MODE),
                os.getenv(
                    "LOOP_DRIFT_JUDGE_REASONING_EFFORT",
                    DEFAULT_CHAT_REASONING_EFFORT,
                ),
            ),
        )
        raw_text = (response.choices[0].message.content or "").strip()
        return _coerce_result(_extract_json_object(raw_text))
    finally:
        await async_client.close()


async def evaluate_drift_zero_shot(
    recent_messages: list[str],
    identity_core: str,
) -> dict[str, Any]:
    """Evaluate identity drift through the configured LLM as a zero-shot judge."""
    try:
        return await _call_drift_judge(recent_messages, identity_core)
    except Exception as exc:
        logger.warning(
            "[DriftDetector] zero-shot judge failed: %s: %s",
            exc.__class__.__name__,
            exc,
        )
        return {
            "consistency_score": 1.0,
            "drift_probability": 0.0,
            "is_drifting": False,
            "reason": "Drift judge unavailable; skipped blocking calibration.",
        }
