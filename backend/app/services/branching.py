"""Global world-line branch helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import models

DEFAULT_BRANCH_ID = "main"


@dataclass(frozen=True)
class BranchAnchor:
    """Parent branch and fork instant for one global world-line branch."""

    branch_id: str
    parent_branch_id: str
    fork_timestamp: datetime


def normalize_branch_id(branch_id: str | None) -> str:
    return (branch_id or "").strip() or DEFAULT_BRANCH_ID


def coerce_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    return timestamp.replace(microsecond=0)


def get_branch_anchor(db: Session, branch_id: str) -> BranchAnchor | None:
    """Return the fork metadata for a non-main global branch, if recorded."""
    normalized_branch_id = normalize_branch_id(branch_id)
    if normalized_branch_id == DEFAULT_BRANCH_ID:
        return None

    events = (
        db.query(models.EventLog)
        .filter(models.EventLog.branch_id == normalized_branch_id)
        .order_by(models.EventLog.timestamp.asc(), models.EventLog.event_id.asc())
        .all()
    )
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fork = payload.get("fork")
        if not isinstance(fork, dict):
            continue

        parent_branch_id = normalize_branch_id(
            str(fork.get("from_branch_id") or DEFAULT_BRANCH_ID),
        )
        fork_timestamp = coerce_timestamp(fork.get("rollback_timestamp"))
        if fork_timestamp is None:
            fork_timestamp = event.timestamp
        return BranchAnchor(
            branch_id=normalized_branch_id,
            parent_branch_id=parent_branch_id,
            fork_timestamp=fork_timestamp,
        )

    return None


def branch_exists(db: Session, branch_id: str) -> bool:
    """Return whether a global branch is known anywhere in EventLog."""
    normalized_branch_id = normalize_branch_id(branch_id)
    if normalized_branch_id == DEFAULT_BRANCH_ID:
        return True
    return (
        db.query(models.EventLog.event_id)
        .filter(models.EventLog.branch_id == normalized_branch_id)
        .first()
        is not None
    )


def get_branch_lineage_ids(
    db: Session,
    branch_id: str,
    visited_branches: set[str] | None = None,
) -> set[str]:
    """Return the selected branch plus its parent branch chain."""
    normalized_branch_id = normalize_branch_id(branch_id)
    visited = set(visited_branches or set())
    if normalized_branch_id in visited:
        return visited

    visited.add(normalized_branch_id)
    anchor = get_branch_anchor(db, normalized_branch_id)
    if anchor is not None:
        return get_branch_lineage_ids(db, anchor.parent_branch_id, visited)
    return visited


def get_global_branch_ids(db: Session) -> list[str]:
    """Return all global world-line branch ids currently known."""
    rows = db.query(models.EventLog.branch_id).distinct().all()
    branch_ids = {
        normalize_branch_id(str(row[0]))
        for row in rows
        if row[0] is not None and normalize_branch_id(str(row[0]))
    }
    return [
        DEFAULT_BRANCH_ID,
        *sorted(branch_id for branch_id in branch_ids if branch_id != DEFAULT_BRANCH_ID),
    ]
