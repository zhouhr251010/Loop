"""Append-only event store helpers for Loop timelines."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.models import utc_now_seconds

DEFAULT_BRANCH_ID = "main"
logger = logging.getLogger(__name__)
MAX_EVENT_LOG_PAYLOAD_PREVIEW = 1200
MAX_EVENT_LOG_STRING_CHARS = 500


def _json_safe(value: Any) -> Any:
    """Convert common runtime objects into JSON-storable values."""
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _coerce_timestamp(value: datetime | None) -> datetime:
    if value is None:
        return utc_now_seconds()
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0)


def _payload_log_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > MAX_EVENT_LOG_STRING_CHARS:
            return "<truncated>"
        return value
    if isinstance(value, dict):
        return {str(key): _payload_log_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_payload_log_value(item) for item in value]
    return value


def _payload_preview(payload: dict[str, Any]) -> str:
    log_payload = _payload_log_value(payload)
    try:
        raw_preview = json.dumps(log_payload, ensure_ascii=False, sort_keys=True)
    except TypeError:
        raw_preview = repr(log_payload)

    if len(raw_preview) <= MAX_EVENT_LOG_PAYLOAD_PREVIEW:
        return raw_preview
    return f"{raw_preview[:MAX_EVENT_LOG_PAYLOAD_PREVIEW]}...<truncated>"


def append_event(
    db: Session,
    *,
    agent_id: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    branch_id: str = DEFAULT_BRANCH_ID,
    timestamp: datetime | None = None,
    commit: bool = True,
) -> models.EventLog:
    """Append one immutable event to the EventLog table."""
    event = models.EventLog(
        agent_id=agent_id,
        branch_id=(branch_id or DEFAULT_BRANCH_ID).strip() or DEFAULT_BRANCH_ID,
        event_type=(event_type or "UNKNOWN_EVENT").strip().upper(),
        payload=_json_safe(payload or {}),
        timestamp=_coerce_timestamp(timestamp),
    )
    db.add(event)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    logger.info(
        "[EventLog Append] event_id=%s agent_id=%s branch_id=%s "
        "event_type=%s timestamp=%s commit=%s payload=%s",
        event.event_id,
        event.agent_id,
        event.branch_id,
        event.event_type,
        event.timestamp.isoformat() if event.timestamp else None,
        commit,
        _payload_preview(event.payload if isinstance(event.payload, dict) else {}),
    )
    return event
