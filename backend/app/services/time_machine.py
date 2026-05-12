"""State reconstruction engine for event-sourced simulation timelines."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.services.branching import (
    DEFAULT_BRANCH_ID,
    get_branch_anchor,
    normalize_branch_id,
)
from app.services.core_memory_service import DEFAULT_CORE_MEMORY, normalize_core_memory

logger = logging.getLogger(__name__)
CURRENT_CORE_MEMORY_FIELD_CHARS = 800
CURRENT_CORE_MEMORY_MAX_CHARS = 2400


class TimeMachine:
    """Replay EventLog records to rebuild an agent's state at one instant."""

    def __init__(self, db: Session):
        self.db = db

    def reconstruct_state(
        self,
        agent_id: int,
        target_timestamp: datetime,
        branch_id: str = "main",
        _visited_branches: set[str] | None = None,
    ) -> dict[str, Any]:
        """Reconstruct compact agent state by replaying EventLog internally.

        EventLog payloads are never exposed to the LLM context. Replay output is
        intentionally reduced to core memory, intimacy, and a short
        current_core_memory string.
        """
        normalized_branch = normalize_branch_id(branch_id)
        target_timestamp = self._coerce_timestamp(target_timestamp)
        visited_branches = set(_visited_branches or set())
        if normalized_branch in visited_branches:
            logger.warning(
                "[Time Machine] Branch ancestry cycle detected for %s.",
                normalized_branch,
            )
            normalized_branch = DEFAULT_BRANCH_ID
        visited_branches.add(normalized_branch)

        anchor = get_branch_anchor(self.db, normalized_branch)
        if anchor is not None and target_timestamp < anchor.fork_timestamp:
            return self.reconstruct_state(
                agent_id=agent_id,
                target_timestamp=target_timestamp,
                branch_id=anchor.parent_branch_id,
                _visited_branches=visited_branches,
            )

        state = self._initial_state(agent_id, target_timestamp, normalized_branch)
        parent_replayed_events = 0
        branch_start_timestamp: datetime | None = None
        if anchor is not None:
            parent_state = self.reconstruct_state(
                agent_id=agent_id,
                target_timestamp=anchor.fork_timestamp,
                branch_id=anchor.parent_branch_id,
                _visited_branches=visited_branches,
            )
            self._load_base_state_from_reconstruction(state, parent_state)
            parent_replayed_events = int(parent_state.get("replayed_events") or 0)
            branch_start_timestamp = anchor.fork_timestamp

        filters = [
            models.EventLog.agent_id == agent_id,
            models.EventLog.branch_id == normalized_branch,
            models.EventLog.timestamp <= target_timestamp,
        ]
        if branch_start_timestamp is not None:
            filters.append(models.EventLog.timestamp >= branch_start_timestamp)
        events = (
            self.db.query(models.EventLog)
            .filter(*filters)
            .order_by(models.EventLog.timestamp.asc(), models.EventLog.event_id.asc())
            .all()
        )
        for event in events:
            self._apply_event(state, event)

        count = len(events)
        state["replayed_events"] = parent_replayed_events + count
        state["current_core_memory"] = self._format_current_core_memory(state)
        logger.info(
            f"[Time Machine] Reconstructing state for Agent {agent_id} at "
            f"{target_timestamp} on branch {normalized_branch}. "
            f"Replayed {count} events.",
        )
        return state

    def _coerce_timestamp(self, value: datetime) -> datetime:
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.replace(microsecond=0)

    def _initial_state(
        self,
        agent_id: int,
        target_timestamp: datetime,
        branch_id: str,
    ) -> dict[str, Any]:
        core_memory = DEFAULT_CORE_MEMORY.copy()
        agent = self.db.get(models.Agent, agent_id)
        if agent is not None and agent.user is not None:
            core_memory = normalize_core_memory(agent.user.core_memory)

        return {
            "agent_id": agent_id,
            "branch_id": branch_id,
            "target_timestamp": target_timestamp,
            "core_memory": core_memory,
            "counterfactual_core_memory": [],
            "current_core_memory": "",
            "working_memory": {},
            "intimacy": {},
            "replayed_events": 0,
        }

    def _apply_event(self, state: dict[str, Any], event: models.EventLog) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        self._load_fork_base_state(state, payload)

        effective_payload = payload.get("counterfactual_event")
        if not isinstance(effective_payload, dict):
            effective_payload = payload

        event_type = str(
            effective_payload.get("event_type") or event.event_type or "",
        ).upper()

        if event_type == "CORE_MEMORY_UPDATED":
            self._apply_core_memory_update(state, effective_payload)
        elif event_type in {"AGENT_CREATED", "AGENT_PROFILE_UPDATED"}:
            self._apply_profile_core_memory(state, effective_payload)
        elif event_type == "COUNTERFACTUAL_EVENT":
            self._apply_counterfactual_event(state, effective_payload)
        elif event_type in {"MESSAGE_RECEIVED", "CHAT_TURN_RECORDED"}:
            self._ignore_message_history_for_prompt_state()
        elif event_type == "RELATIONSHIP_CHANGED":
            self._apply_relationship_changed(state, effective_payload)
        elif event_type == "WORKING_MEMORY_CLEARED":
            state["working_memory"] = {}

        state_patch = effective_payload.get("state_patch")
        if isinstance(state_patch, dict):
            self._merge_state_patch(state, state_patch)

    def _load_fork_base_state(
        self,
        state: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        fork = payload.get("fork")
        if not isinstance(fork, dict):
            return
        base_state = fork.get("base_state")
        if not isinstance(base_state, dict):
            return

        if isinstance(base_state.get("core_memory"), dict):
            state["core_memory"] = normalize_core_memory(base_state["core_memory"])
        if isinstance(base_state.get("current_core_memory"), str):
            state["current_core_memory"] = base_state["current_core_memory"].strip()
        if isinstance(base_state.get("intimacy"), dict):
            state["intimacy"] = {
                str(key): float(value)
                for key, value in base_state["intimacy"].items()
                if isinstance(value, (int, float))
            }

    def _load_base_state_from_reconstruction(
        self,
        state: dict[str, Any],
        base_state: dict[str, Any],
    ) -> None:
        """Seed a branch with inherited parent-world state."""
        if isinstance(base_state.get("core_memory"), dict):
            state["core_memory"] = normalize_core_memory(base_state["core_memory"])
        if isinstance(base_state.get("current_core_memory"), str):
            state["current_core_memory"] = base_state["current_core_memory"].strip()
        if isinstance(base_state.get("intimacy"), dict):
            state["intimacy"] = {
                str(key): float(value)
                for key, value in base_state["intimacy"].items()
                if isinstance(value, (int, float))
            }
        if isinstance(base_state.get("working_memory"), dict):
            state["working_memory"] = base_state["working_memory"]

    def _apply_core_memory_update(
        self,
        state: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        core_memory = payload.get("core_memory")
        if isinstance(core_memory, dict):
            state["core_memory"] = normalize_core_memory(core_memory)
            return

        key = str(payload.get("key") or "").strip()
        if not key:
            return
        updated_memory = normalize_core_memory(state["core_memory"])
        updated_memory[key] = str(payload.get("new_value") or "")[:8000]
        state["core_memory"] = normalize_core_memory(updated_memory)

    def _apply_profile_core_memory(
        self,
        state: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        core_memory = payload.get("core_memory")
        if isinstance(core_memory, dict):
            state["core_memory"] = normalize_core_memory(core_memory)

    def _apply_counterfactual_event(
        self,
        state: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        counterfactual_text = self._counterfactual_text(payload)
        if not counterfactual_text:
            return

        counterfactual_line = f"[COUNTERFACTUAL OVERRIDE] {counterfactual_text}"
        overrides = state.setdefault("counterfactual_core_memory", [])
        if counterfactual_line not in overrides:
            overrides.insert(0, counterfactual_line)

        core_memory = normalize_core_memory(state["core_memory"])
        existing_traits = core_memory["persona_traits"].strip()
        if counterfactual_line not in existing_traits:
            core_memory["persona_traits"] = (
                f"{counterfactual_line}\n{existing_traits}".strip()
            )[-8000:]
        state["core_memory"] = normalize_core_memory(core_memory)

    def _counterfactual_text(self, payload: dict[str, Any]) -> str:
        for key in ("description", "fact", "text", "content", "memory"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return str(payload).strip() if payload else ""

    def _ignore_message_history_for_prompt_state(self) -> None:
        """Keep replayed chat events physically isolated from LLM prompt state."""
        return None

    def _apply_relationship_changed(
        self,
        state: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        target_agent_id = payload.get("target_agent_id") or payload.get("agent_id_2")
        if target_agent_id is None:
            return
        key = str(target_agent_id)
        intimacy = state.setdefault("intimacy", {})
        if isinstance(payload.get("affinity_score"), (int, float)):
            intimacy[key] = float(payload["affinity_score"])
        elif isinstance(payload.get("affinity_change"), (int, float)):
            intimacy[key] = float(intimacy.get(key, 0.0)) + float(
                payload["affinity_change"],
            )

    def _merge_state_patch(
        self,
        state: dict[str, Any],
        state_patch: dict[str, Any],
    ) -> None:
        if isinstance(state_patch.get("core_memory"), dict):
            state["core_memory"] = normalize_core_memory(state_patch["core_memory"])
        if isinstance(state_patch.get("intimacy"), dict):
            state["intimacy"] = {
                str(key): float(value)
                for key, value in state_patch["intimacy"].items()
                if isinstance(value, (int, float))
            }

    def _format_current_core_memory(self, state: dict[str, Any]) -> str:
        core_memory = normalize_core_memory(state.get("core_memory"))
        counterfactual_overrides = [
            str(item).strip()
            for item in state.get("counterfactual_core_memory", [])
            if str(item).strip()
        ]
        lines = [*counterfactual_overrides]
        for key in ("persona_traits", "key_relationships", "current_goals"):
            value = core_memory.get(key, "").strip()
            if key == "persona_traits" and counterfactual_overrides:
                value = "\n".join(
                    line
                    for line in value.splitlines()
                    if line.strip() not in counterfactual_overrides
                    and not self._conflicts_with_counterfactual(
                        line,
                        counterfactual_overrides,
                    )
                ).strip()
            if value:
                lines.append(f"{key}: {self._shorten(value, CURRENT_CORE_MEMORY_FIELD_CHARS)}")
        return self._shorten("\n".join(lines).strip(), CURRENT_CORE_MEMORY_MAX_CHARS)

    def _shorten(self, value: str, limit: int) -> str:
        clean_value = (value or "").strip()
        if len(clean_value) <= limit:
            return clean_value
        return f"{clean_value[:limit]}...[truncated]"

    def _conflicts_with_counterfactual(
        self,
        baseline_line: str,
        counterfactual_overrides: list[str],
    ) -> bool:
        baseline_tokens = self._semantic_tokens(baseline_line)
        if not baseline_tokens:
            return False
        for override in counterfactual_overrides:
            overlap = baseline_tokens & self._semantic_tokens(override)
            if len(overlap) >= 2:
                return True
        return False

    def _semantic_tokens(self, text: str) -> set[str]:
        normalized = text.lower()
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
        cjk_bigrams = {
            "".join(cjk_chars[index : index + 2])
            for index in range(max(len(cjk_chars) - 1, 0))
        }
        latin_words = set(re.findall(r"[a-z0-9_]{3,}", normalized))
        stop_tokens = {"用户", "绝对", "不能", "必须", "这是", "一个", "作为"}
        return (cjk_bigrams | latin_words) - stop_tokens
