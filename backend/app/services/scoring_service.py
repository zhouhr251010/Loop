"""Scoring helpers for IPIP-NEO-120 and PVQ-21 questionnaire probes."""

from __future__ import annotations

from typing import Any, Mapping

from app.services.core_memory_service import normalize_core_memory


QUESTIONNAIRE_PROFILE_START = "[questionnaire_scoring_profile]"
QUESTIONNAIRE_PROFILE_END = "[/questionnaire_scoring_profile]"


IPIP_DOMAIN_BY_CODE = {
    "N": "Neuroticism",
    "E": "Extraversion",
    "O": "Openness",
    "A": "Agreeableness",
    "C": "Conscientiousness",
}

IPIP_FACET_LABELS = {
    "N1": "Anxiety",
    "N2": "Anger",
    "N3": "Depression",
    "N4": "Self-Consciousness",
    "N5": "Immoderation",
    "N6": "Vulnerability",
    "E1": "Friendliness",
    "E2": "Gregariousness",
    "E3": "Assertiveness",
    "E4": "Activity Level",
    "E5": "Excitement Seeking",
    "E6": "Cheerfulness",
    "O1": "Imagination",
    "O2": "Artistic Interests",
    "O3": "Emotionality",
    "O4": "Adventurousness",
    "O5": "Intellect",
    "O6": "Liberalism",
    "A1": "Trust",
    "A2": "Morality",
    "A3": "Altruism",
    "A4": "Cooperation",
    "A5": "Modesty",
    "A6": "Sympathy",
    "C1": "Self-Efficacy",
    "C2": "Orderliness",
    "C3": "Dutifulness",
    "C4": "Achievement-Striving",
    "C5": "Self-Discipline",
    "C6": "Cautiousness",
}


def _ipip_id(item_number: int) -> str:
    return f"IPIP_{item_number:03d}"


def _pvq_id(item_number: int) -> str:
    return f"PVQ_{item_number:03d}"


ScoringMap = dict[str, dict[str, Any]]


def _build_ipip_scoring_map() -> ScoringMap:
    facet_items: dict[str, list[tuple[int, bool]]] = {
        "N1": [(1, False), (31, False), (61, False), (91, False)],
        "N2": [(6, False), (36, False), (66, False), (96, True)],
        "N3": [(11, False), (41, False), (71, False), (101, True)],
        "N4": [(16, False), (46, False), (76, False), (106, True)],
        "N5": [(21, False), (51, True), (81, True), (111, True)],
        "N6": [(26, False), (56, False), (86, False), (116, True)],
        "E1": [(2, False), (32, False), (62, True), (92, True)],
        "E2": [(7, False), (37, False), (67, True), (97, True)],
        "E3": [(12, False), (42, False), (72, False), (102, True)],
        "E4": [(17, False), (47, False), (77, False), (107, True)],
        "E5": [(22, False), (52, False), (82, False), (112, False)],
        "E6": [(27, False), (57, False), (87, False), (117, False)],
        "O1": [(3, False), (33, False), (63, False), (93, False)],
        "O2": [(8, False), (38, False), (68, True), (98, True)],
        "O3": [(13, False), (43, False), (73, True), (103, True)],
        "O4": [(18, False), (48, True), (78, True), (108, True)],
        "O5": [(23, False), (53, True), (83, True), (113, True)],
        "O6": [(28, False), (58, False), (88, True), (118, True)],
        "A1": [(4, False), (34, False), (64, False), (94, True)],
        "A2": [(9, True), (39, True), (69, True), (99, True)],
        "A3": [(14, False), (44, False), (74, True), (104, True)],
        "A4": [(19, True), (49, True), (79, True), (109, True)],
        "A5": [(24, True), (54, True), (84, True), (114, True)],
        "A6": [(29, False), (59, False), (89, True), (119, True)],
        "C1": [(5, False), (35, False), (65, False), (95, False)],
        "C2": [(10, False), (40, True), (70, True), (100, True)],
        "C3": [(15, False), (45, False), (75, True), (105, True)],
        "C4": [(20, False), (50, False), (80, True), (110, True)],
        "C5": [(25, False), (55, False), (85, True), (115, True)],
        "C6": [(30, True), (60, True), (90, True), (120, True)],
    }
    scoring_map: ScoringMap = {}
    for facet_code, items in facet_items.items():
        dimension = IPIP_DOMAIN_BY_CODE[facet_code[0]]
        facet = IPIP_FACET_LABELS[facet_code]
        for item_number, reverse in items:
            scoring_map[_ipip_id(item_number)] = {
                "dimension": dimension,
                "facet": facet,
                "reverse": reverse,
            }
    return scoring_map


IPIP_SCORING_MAP = _build_ipip_scoring_map()

PVQ_SCORING_MAP: ScoringMap = {
    _pvq_id(item): {"dimension": "Self-Direction", "reverse": False}
    for item in (1, 11)
} | {
    _pvq_id(item): {"dimension": "Stimulation", "reverse": False}
    for item in (6, 15)
} | {
    _pvq_id(item): {"dimension": "Hedonism", "reverse": False}
    for item in (10, 21)
} | {
    _pvq_id(item): {"dimension": "Achievement", "reverse": False}
    for item in (4, 13)
} | {
    _pvq_id(item): {"dimension": "Power", "reverse": False}
    for item in (2, 17)
} | {
    _pvq_id(item): {"dimension": "Security", "reverse": False}
    for item in (5, 14)
} | {
    _pvq_id(item): {"dimension": "Conformity", "reverse": False}
    for item in (7, 16)
} | {
    _pvq_id(item): {"dimension": "Tradition", "reverse": False}
    for item in (9, 20)
} | {
    _pvq_id(item): {"dimension": "Benevolence", "reverse": False}
    for item in (12, 18)
} | {
    _pvq_id(item): {"dimension": "Universalism", "reverse": False}
    for item in (3, 8, 19)
}


def score_ipip120(answers: Mapping[str, Any]) -> dict[str, Any]:
    """Score IPIP-NEO-120 item answers into Big Five domains and facets."""
    return _score_questionnaire(
        answers=answers,
        scoring_map=IPIP_SCORING_MAP,
        scale_max=5,
        instrument="IPIP-NEO-120",
        include_facets=True,
    )


def score_pvq21(answers: Mapping[str, Any]) -> dict[str, Any]:
    """Score PVQ-21 item answers into Schwartz value dimensions."""
    return _score_questionnaire(
        answers=answers,
        scoring_map=PVQ_SCORING_MAP,
        scale_max=6,
        instrument="PVQ-21",
        include_facets=False,
    )


def score_questionnaire_payload(
    big_five_scores: Mapping[str, Any] | None,
    schwartz_values: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Score raw questionnaire item payloads while preserving legacy aggregate input."""
    scored_big_five = (
        score_ipip120(big_five_scores)
        if _contains_any_item(big_five_scores, "IPIP_")
        else dict(big_five_scores or {})
    )
    scored_schwartz = (
        score_pvq21(schwartz_values)
        if _contains_any_item(schwartz_values, "PVQ_")
        else dict(schwartz_values or {})
    )
    return scored_big_five, scored_schwartz


def score_probe_responses(
    responses: list[Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Score a bulk /api/probes/submit request if it contains IPIP/PVQ items."""
    ipip_answers: dict[str, Any] = {}
    pvq_answers: dict[str, Any] = {}
    for response in responses:
        probe_set = str(getattr(response, "probe_set", "") or "").upper()
        probe_id = str(getattr(response, "probe_id", "") or "")
        answer = getattr(response, "answer", None)
        if probe_set in {"IPIP120", "IPIP-120", "IPIP_NEO_120"}:
            ipip_answers[probe_id] = answer
        elif probe_set in {"PVQ21", "PVQ-21"}:
            pvq_answers[probe_id] = answer

    return (
        score_ipip120(ipip_answers) if ipip_answers else None,
        score_pvq21(pvq_answers) if pvq_answers else None,
    )


def merge_questionnaire_scores_into_core_memory(
    current_core_memory: Any,
    mbti_type: str | None,
    big_five_scores: Mapping[str, Any] | None,
    schwartz_values: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Insert or replace the questionnaire-derived profile block in core memory."""
    core_memory = normalize_core_memory(current_core_memory)
    profile_block = _build_questionnaire_profile_block(
        mbti_type=mbti_type,
        big_five_scores=big_five_scores,
        schwartz_values=schwartz_values,
    )
    if not profile_block:
        return core_memory

    existing_traits = _remove_existing_profile_block(core_memory["persona_traits"])
    merged_traits = "\n".join(
        part for part in (existing_traits.strip(), profile_block) if part
    )
    core_memory["persona_traits"] = merged_traits[-8000:]
    return core_memory


def compact_score_summary(scores: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a log-safe score summary without raw item answers."""
    if not isinstance(scores, Mapping):
        return {}

    domains = _extract_means(scores, "domains")
    if not domains:
        domains = _extract_means(scores, "")

    summary: dict[str, Any] = {
        "instrument": scores.get("instrument"),
        "domains": {key: round(value, 3) for key, value in domains.items()},
    }
    if "items_scored" in scores:
        summary["items_scored"] = scores.get("items_scored")
    if "items_expected" in scores:
        summary["items_expected"] = scores.get("items_expected")
    missing_items = scores.get("missing_items")
    if isinstance(missing_items, list):
        summary["missing_count"] = len(missing_items)
    return {key: value for key, value in summary.items() if value not in (None, {})}


def _score_questionnaire(
    answers: Mapping[str, Any],
    scoring_map: Mapping[str, Mapping[str, Any]],
    scale_max: int,
    instrument: str,
    include_facets: bool,
) -> dict[str, Any]:
    dimension_buckets: dict[str, list[float]] = {}
    facet_buckets: dict[str, list[float]] = {}
    items_scored: dict[str, dict[str, Any]] = {}
    missing_items: list[str] = []

    for item_id, item in sorted(scoring_map.items()):
        raw_score = _extract_numeric_answer(answers.get(item_id))
        if raw_score is None:
            missing_items.append(item_id)
            continue
        if not 1 <= raw_score <= scale_max:
            missing_items.append(item_id)
            continue

        reverse = bool(item.get("reverse"))
        dimension = str(item.get("dimension") or "")
        facet = item.get("facet")
        scored_value = scale_max + 1 - raw_score if reverse else raw_score
        dimension_buckets.setdefault(dimension, []).append(scored_value)
        if include_facets and facet:
            facet_buckets.setdefault(str(facet), []).append(scored_value)
        items_scored[item_id] = {
            "raw": raw_score,
            "score": scored_value,
            "dimension": dimension,
            "facet": facet,
            "reverse": reverse,
        }

    result: dict[str, Any] = {
        "instrument": instrument,
        "scale_min": 1,
        "scale_max": scale_max,
        "reverse_formula": f"{scale_max + 1} - raw_value",
        "domains": _summarize_buckets(dimension_buckets),
        "items_scored": len(items_scored),
        "items_expected": len(scoring_map),
        "missing_items": missing_items,
    }
    if include_facets:
        result["facets"] = _summarize_buckets(facet_buckets)
    return result


def _summarize_buckets(buckets: Mapping[str, list[float]]) -> dict[str, dict[str, Any]]:
    return {
        label: {
            "mean": round(sum(values) / len(values), 3),
            "sum": round(sum(values), 3),
            "count": len(values),
        }
        for label, values in sorted(buckets.items())
        if values
    }


def _extract_numeric_answer(raw_answer: Any) -> float | None:
    if isinstance(raw_answer, bool) or raw_answer is None:
        return None
    if isinstance(raw_answer, (int, float)):
        return float(raw_answer)
    if isinstance(raw_answer, str):
        try:
            return float(raw_answer.strip())
        except ValueError:
            return None
    if isinstance(raw_answer, Mapping):
        for key in ("value", "score", "answer"):
            value = _extract_numeric_answer(raw_answer.get(key))
            if value is not None:
                return value
    return None


def _contains_any_item(payload: Mapping[str, Any] | None, prefix: str) -> bool:
    if not isinstance(payload, Mapping):
        return False
    return any(str(key).startswith(prefix) for key in payload)


def _build_questionnaire_profile_block(
    mbti_type: str | None,
    big_five_scores: Mapping[str, Any] | None,
    schwartz_values: Mapping[str, Any] | None,
) -> str:
    lines: list[str] = []
    clean_mbti = (mbti_type or "").strip().upper()
    if clean_mbti:
        lines.append(f"MBTI: {clean_mbti}")

    domain_means = _extract_means(big_five_scores, "domains")
    if domain_means:
        lines.append(f"Big Five: {_format_ranked_scores(domain_means)}")

    facet_means = _extract_means(big_five_scores, "facets")
    if facet_means:
        lines.append(
            f"Salient IPIP facets: {_format_ranked_scores(facet_means, limit=6)}",
        )

    value_means = _extract_means(schwartz_values, "domains")
    if value_means:
        lines.append(f"Schwartz values: {_format_ranked_scores(value_means)}")
    elif schwartz_values:
        numeric_values = {
            _humanize_key(key): float(value)
            for key, value in schwartz_values.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if numeric_values:
            lines.append(f"Schwartz values: {_format_ranked_scores(numeric_values)}")

    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"{QUESTIONNAIRE_PROFILE_START}\n{body}\n{QUESTIONNAIRE_PROFILE_END}"


def _extract_means(payload: Mapping[str, Any] | None, key: str) -> dict[str, float]:
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get(key)
    if isinstance(nested, Mapping):
        return {
            str(label): float(stats["mean"])
            for label, stats in nested.items()
            if isinstance(stats, Mapping)
            and isinstance(stats.get("mean"), (int, float))
            and not isinstance(stats.get("mean"), bool)
        }
    return {
        _humanize_key(label): float(value)
        for label, value in payload.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _format_ranked_scores(scores: Mapping[str, float], limit: int | None = None) -> str:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if limit is not None:
        ranked = ranked[:limit]
    return ", ".join(f"{label}={score:.2f}" for label, score in ranked)


def _humanize_key(key: Any) -> str:
    return str(key).replace("_", " ").replace("-", " ").title()


def _remove_existing_profile_block(value: str) -> str:
    text = (value or "").strip()
    start = text.find(QUESTIONNAIRE_PROFILE_START)
    end = text.find(QUESTIONNAIRE_PROFILE_END)
    if start == -1 or end == -1 or end < start:
        return text
    end += len(QUESTIONNAIRE_PROFILE_END)
    return f"{text[:start].strip()}\n{text[end:].strip()}".strip()
