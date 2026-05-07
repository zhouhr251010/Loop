"""Export research interaction data as JSONL fine-tuning datasets."""

import json

from sqlalchemy.orm import Session

from app import models


def _to_jsonl(rows: list[dict]) -> str:
    """Serialize training rows as UTF-8 friendly JSONL text."""
    return "\n".join(
        json.dumps(row, ensure_ascii=False)
        for row in rows
    )


def _system_prompt_for_agent(agent: models.Agent) -> str:
    """Return the persona/system prompt used for supervised chat samples."""
    return agent.system_prompt_base or (
        "你是 Loop 计算社会模拟中的用户数字分身。"
        "请用符合该用户人格、价值观和表达习惯的方式回复。"
    )


def export_chatlogs_to_jsonl(db: Session, user_id: int) -> str:
    """Export one user's private chat turns in OpenAI/DeepSeek SFT JSONL format."""
    chat_logs = (
        db.query(models.ChatLog)
        .join(models.Agent)
        .filter(models.Agent.user_id == user_id)
        .order_by(models.ChatLog.timestamp.asc(), models.ChatLog.id.asc())
        .all()
    )

    rows: list[dict] = []
    for chat_log in chat_logs:
        rows.append(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": _system_prompt_for_agent(chat_log.agent),
                    },
                    {
                        "role": "user",
                        "content": chat_log.user_message,
                    },
                    {
                        "role": "assistant",
                        "content": chat_log.agent_reply,
                    },
                ],
            },
        )

    return _to_jsonl(rows)


def export_feedback_to_jsonl(db: Session, user_id: int) -> str:
    """Export one user's plaza correction feedback in SFT JSONL format."""
    feedback_logs = (
        db.query(models.FeedbackLog)
        .filter(models.FeedbackLog.user_id == user_id)
        .order_by(models.FeedbackLog.timestamp.asc(), models.FeedbackLog.id.asc())
        .all()
    )

    rows: list[dict] = []
    for feedback_log in feedback_logs:
        rows.append(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "请用符合你人设的语气改写这句话："
                            f"{feedback_log.original_text}"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": feedback_log.corrected_text,
                    },
                ],
            },
        )

    return _to_jsonl(rows)
