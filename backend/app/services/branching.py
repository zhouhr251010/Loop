"""Global world-line branch helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app import models

DEFAULT_BRANCH_ID = "main"


@dataclass(frozen=True)
class BranchAnchor:
    """Parent branch and fork instant for one global world-line branch."""

    branch_id: str
    parent_branch_id: str
    fork_timestamp: datetime
    parent_event_id: int | None = None
    parent_event_branch_id: str | None = None


@dataclass(frozen=True)
class BranchReadWindow:
    """A visible slice of one branch while reading a world-line."""

    branch_id: str
    until_timestamp: datetime | None = None
    until_event_id: int | None = None


def normalize_branch_id(branch_id: str | None) -> str:
    return (branch_id or "").strip() or DEFAULT_BRANCH_ID


def branch_scope_ids(branch_id: str | None) -> list[str]:
    """Return the Git-style readable branch scope for one context."""
    normalized_branch_id = normalize_branch_id(branch_id)
    if normalized_branch_id == DEFAULT_BRANCH_ID:
        return [DEFAULT_BRANCH_ID]
    return [DEFAULT_BRANCH_ID, normalized_branch_id]


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        parent_event_id = _safe_int(fork.get("parent_event_id"))
        parent_event_branch_id = normalize_branch_id(
            str(fork.get("parent_event_branch_id") or ""),
        )
        if parent_event_branch_id == DEFAULT_BRANCH_ID and not fork.get(
            "parent_event_branch_id",
        ):
            parent_event_branch_id = None
        if parent_event_id is not None and parent_event_branch_id is None:
            parent_event = db.get(models.EventLog, parent_event_id)
            if parent_event is not None:
                parent_event_branch_id = normalize_branch_id(parent_event.branch_id)

        if parent_event_id is None:
            latest_parent_event = (
                db.query(models.EventLog.event_id)
                .filter(
                    models.EventLog.branch_id == parent_branch_id,
                    models.EventLog.timestamp <= fork_timestamp,
                )
                .order_by(
                    models.EventLog.timestamp.desc(),
                    models.EventLog.event_id.desc(),
                )
                .first()
            )
            if latest_parent_event is not None:
                parent_event_id = int(latest_parent_event[0])
                parent_event_branch_id = parent_branch_id

        return BranchAnchor(
            branch_id=normalized_branch_id,
            parent_branch_id=parent_branch_id,
            fork_timestamp=fork_timestamp,
            parent_event_id=parent_event_id,
            parent_event_branch_id=parent_event_branch_id or parent_branch_id,
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


def get_branch_read_windows(
    db: Session,
    branch_id: str | None,
    until_timestamp: datetime | None = None,
    visited_branches: set[str] | None = None,
) -> list[BranchReadWindow]:
    """Return branch slices visible from one world-line.

    A fork inherits each ancestor only up to the fork point. After that point,
    parent and child branches are independent worlds.
    """
    normalized_branch_id = normalize_branch_id(branch_id)
    cutoff = coerce_timestamp(until_timestamp) if until_timestamp is not None else None
    visited = set(visited_branches or set())
    if normalized_branch_id in visited:
        return [BranchReadWindow(DEFAULT_BRANCH_ID, cutoff)]
    visited.add(normalized_branch_id)

    current_window = BranchReadWindow(
        branch_id=normalized_branch_id,
        until_timestamp=cutoff,
    )
    anchor = get_branch_anchor(db, normalized_branch_id)
    if anchor is None:
        return [current_window]

    parent_cutoff = anchor.fork_timestamp
    if cutoff is not None and cutoff < parent_cutoff:
        parent_cutoff = cutoff
    parent_windows = get_branch_read_windows(
        db,
        anchor.parent_branch_id,
        parent_cutoff,
        visited,
    )
    if anchor.parent_event_id is not None:
        parent_event_branch_id = normalize_branch_id(
            anchor.parent_event_branch_id or anchor.parent_branch_id,
        )
        parent_windows = [
            BranchReadWindow(
                branch_id=window.branch_id,
                until_timestamp=window.until_timestamp,
                until_event_id=anchor.parent_event_id
                if window.branch_id == parent_event_branch_id
                else window.until_event_id,
            )
            for window in parent_windows
        ]
    return [*parent_windows, current_window]


def branch_window_filter(
    branch_column: Any,
    timestamp_column: Any,
    id_column: Any | None,
    windows: list[BranchReadWindow],
) -> Any:
    """Build a SQLAlchemy visibility filter from branch read windows."""
    clauses = []
    for window in windows:
        clause = branch_column == window.branch_id
        if window.until_timestamp is not None:
            if window.until_event_id is not None and id_column is not None:
                clause = and_(
                    clause,
                    or_(
                        timestamp_column < window.until_timestamp,
                        and_(
                            timestamp_column == window.until_timestamp,
                            id_column <= window.until_event_id,
                        ),
                    ),
                )
            else:
                clause = and_(clause, timestamp_column <= window.until_timestamp)
        clauses.append(clause)
    return or_(*clauses)


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
