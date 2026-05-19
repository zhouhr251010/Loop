"""Database operations for Boundary-1-isolated N-to-N chat groups."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import models


def _normalize_group_type(group_type: str | models.GroupType) -> str:
    value = getattr(group_type, "value", group_type)
    normalized = str(value or "").strip().upper()
    allowed_values = {item.value for item in models.GroupType}
    if normalized not in allowed_values:
        raise ValueError(f"group_type must be one of: {', '.join(sorted(allowed_values))}")
    return normalized


def _normalize_entity_type(entity_type: str | models.GroupEntityType) -> str:
    value = getattr(entity_type, "value", entity_type)
    normalized = str(value or "").strip().upper()
    allowed_values = {item.value for item in models.GroupEntityType}
    if normalized not in allowed_values:
        raise ValueError(
            f"entity_type must be one of: {', '.join(sorted(allowed_values))}",
        )
    return normalized


def create_group(
    db: Session,
    *,
    name: str,
    group_type: str | models.GroupType,
    owner_id: int | None = None,
    topic: str | None = None,
) -> models.Group:
    """Create one human-only or agent-only chat group."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Group name must not be blank.")

    group = models.Group(
        name=clean_name[:128],
        owner_id=owner_id,
        topic=(topic or "").strip()[:255] or None,
        group_type=_normalize_group_type(group_type),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def get_group(db: Session, group_id: str) -> models.Group | None:
    """Return one group by id."""
    normalized_group_id = str(group_id or "").strip()
    if not normalized_group_id:
        return None
    return db.query(models.Group).filter(models.Group.id == normalized_group_id).first()


def list_group_members(db: Session, group_id: str) -> list[models.GroupMember]:
    """Return members in insertion order for one group."""
    normalized_group_id = str(group_id or "").strip()
    if not normalized_group_id:
        return []
    return (
        db.query(models.GroupMember)
        .filter(models.GroupMember.group_id == normalized_group_id)
        .order_by(models.GroupMember.id.asc())
        .all()
    )


def _ensure_entity_exists(
    db: Session,
    *,
    entity_id: str,
    entity_type: str,
) -> None:
    try:
        numeric_entity_id = int(entity_id)
    except ValueError as exc:
        raise ValueError("entity_id must be an integer string.") from exc

    if entity_type == models.GroupEntityType.USER.value:
        exists = (
            db.query(models.User.id)
            .filter(models.User.id == numeric_entity_id)
            .first()
            is not None
        )
        if not exists:
            raise ValueError("User entity not found.")
        return

    exists = (
        db.query(models.Agent.id)
        .filter(models.Agent.id == numeric_entity_id)
        .first()
        is not None
    )
    if not exists:
        raise ValueError("Agent entity not found.")


def _enforce_boundary_1(group: models.Group, entity_type: str) -> None:
    group_type = str(group.group_type or "").strip().upper()
    if (
        group_type == models.GroupType.HUMAN_ONLY.value
        and entity_type == models.GroupEntityType.AGENT.value
    ):
        raise ValueError("Boundary 1 Violation: Cannot add Agent to HUMAN_ONLY group")
    if (
        group_type == models.GroupType.AGENT_ONLY.value
        and entity_type == models.GroupEntityType.USER.value
    ):
        raise ValueError("Boundary 1 Violation: Cannot add User to AGENT_ONLY group")


def add_group_member(
    group_id: str,
    entity_id: str,
    entity_type: str | models.GroupEntityType,
    db: Session,
    *,
    role: str = "member",
) -> models.GroupMember:
    """Add one member after enforcing Boundary 1 proton isolation."""
    group = get_group(db, group_id)
    if group is None:
        raise ValueError("Group not found.")

    normalized_entity_id = str(entity_id or "").strip()
    if not normalized_entity_id:
        raise ValueError("entity_id must not be blank.")

    normalized_entity_type = _normalize_entity_type(entity_type)
    _enforce_boundary_1(group, normalized_entity_type)
    _ensure_entity_exists(
        db,
        entity_id=normalized_entity_id,
        entity_type=normalized_entity_type,
    )

    existing_member = (
        db.query(models.GroupMember)
        .filter(
            models.GroupMember.group_id == group.id,
            models.GroupMember.entity_id == normalized_entity_id,
            models.GroupMember.entity_type == normalized_entity_type,
        )
        .first()
    )
    if existing_member is not None:
        return existing_member

    member = models.GroupMember(
        group_id=group.id,
        entity_id=normalized_entity_id,
        entity_type=normalized_entity_type,
        role=(role or "member").strip()[:32] or "member",
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def remove_group_member(
    db: Session,
    *,
    group_id: str,
    entity_id: str,
    entity_type: str | models.GroupEntityType,
) -> bool:
    """Remove one group member if present."""
    normalized_entity_type = _normalize_entity_type(entity_type)
    member = (
        db.query(models.GroupMember)
        .filter(
            models.GroupMember.group_id == str(group_id or "").strip(),
            models.GroupMember.entity_id == str(entity_id or "").strip(),
            models.GroupMember.entity_type == normalized_entity_type,
        )
        .first()
    )
    if member is None:
        return False
    db.delete(member)
    db.commit()
    return True
