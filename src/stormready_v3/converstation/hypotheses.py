from __future__ import annotations

from typing import Any

from stormready_v3.prediction.priors import REGIME_GRADUATION_THRESHOLDS
from stormready_v3.storage.db import Database
from stormready_v3.operator_text import communication_payload

from .equation_links import equation_link_for_runtime_target, equation_priority_for_runtime_target
from .memory import ConversationMemoryService


_LOCATION_TARGET_CONFIGS: dict[str, dict[str, Any]] = {
    "transit_relevance": {
        "hypothesis_key": "learn::transit_relevance",
        "dependency_groups": ("access",),
        "question_target": "transit_relevance",
        "agenda_key": "qualitative_confirm::transit",
        "label": "nearby transit or access",
        "expected_impact": "Helps me weight access signals correctly.",
        "impact_phrase": "That changes how much I should trust access signals in the forecast.",
    },
    "venue_relevance": {
        "hypothesis_key": "learn::venue_relevance",
        "dependency_groups": ("venue", "events"),
        "question_target": "venue_relevance",
        "agenda_key": "qualitative_confirm::venue",
        "label": "nearby venues or event nights",
        "expected_impact": "Helps me weight venue-linked demand correctly.",
        "impact_phrase": "That changes how much I should trust venue and event signals in the forecast.",
    },
    "hotel_travel_relevance": {
        "hypothesis_key": "learn::hotel_travel_relevance",
        "dependency_groups": ("travel", "tourism", "hotel"),
        "question_target": "hotel_travel_relevance",
        "agenda_key": "qualitative_confirm::travel",
        "label": "nearby hotels or travel hubs",
        "expected_impact": "Helps me weight travel-linked demand correctly.",
        "impact_phrase": "That changes how much I should trust hotel and travel signals in the forecast.",
    },
}


class LearningHypothesisService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.memory = ConversationMemoryService(db)

    def sync(self, *, operator_id: str) -> None:
        progress = self._load_learning_progress(operator_id)
        raw_groups = self.memory.aggregate_recent_observations(operator_id, lookback_days=45, limit=30)
        groups = _merge_observation_groups(raw_groups)
        grouped_by_target = {
            str(group.get("runtime_target") or ""): group
            for group in groups
            if str(group.get("runtime_target") or "")
        }

        synthesized: list[dict[str, Any]] = []
        synthesized.extend(self._synthesize_location_relevance_hypotheses(operator_id, grouped_by_target, progress))
        synthesized.extend(self._synthesize_observation_hypotheses(groups, progress))

        for hypothesis in synthesized:
            current = self.memory.get_hypothesis(operator_id, hypothesis["hypothesis_key"])
            self.memory.upsert_hypothesis(
                operator_id=operator_id,
                hypothesis_key=hypothesis["hypothesis_key"],
                confidence=hypothesis["confidence"],
                hypothesis_value=hypothesis["hypothesis_value"],
                evidence=hypothesis["evidence"],
                increment_trigger=False,
            )
            self._log_hypothesis_sync(
                operator_id=operator_id,
                current=current,
                hypothesis=hypothesis,
            )

    def _synthesize_observation_hypotheses(
        self,
        groups: list[dict[str, Any]],
        progress: dict[str, Any],
    ) -> list[dict[str, Any]]:
        synthesized: list[dict[str, Any]] = []
        for group in groups:
            runtime_target = str(group.get("runtime_target") or "")
            if runtime_target in _LOCATION_TARGET_CONFIGS:
                continue
            count = int(group.get("observation_count") or 0)
            if count < 2:
                continue
            mapped = _map_observation_group_to_hypothesis(group)
            if mapped is None:
                continue
            confidence = "high" if count >= 3 else "medium"
            equation_link = equation_link_for_runtime_target(runtime_target)
            evidence_bundle = {
                "operator_observation": {
                    "observation_count": count,
                    "observation_type": group.get("observation_type"),
                    "direction": group.get("direction"),
                    "last_service_date": group.get("last_service_date"),
                }
            }
            synthesized.append(
                {
                    "hypothesis_key": mapped["hypothesis_key"],
                    "confidence": confidence,
                    "hypothesis_value": {
                        **mapped,
                        "equation_terms": equation_link.get("equation_terms", []),
                        "equation_path": equation_link.get("equation_path"),
                        "equation_influence_mode": equation_link.get("influence_mode"),
                        "learning_stage": progress["stage"],
                        "learning_progress": progress,
                        "question_ready": True,
                        "promotion_readiness": _promotion_readiness(mapped),
                        "evidence_sources": ["operator_observation"],
                        "evidence_bundle": evidence_bundle,
                        "agenda_priority": equation_priority_for_runtime_target(runtime_target),
                        "observation_count": count,
                        "last_seen_at": group.get("last_seen_at"),
                        "last_service_date": group.get("last_service_date"),
                    },
                    "evidence": {
                        "source": "operator_observation_log",
                        "observation_count": count,
                        "runtime_target": runtime_target,
                        "observation_type": group.get("observation_type"),
                        "direction": group.get("direction"),
                        "last_service_date": group.get("last_service_date"),
                        "learning_stage": progress["stage"],
                    },
                }
            )
        return synthesized

    def _synthesize_location_relevance_hypotheses(
        self,
        operator_id: str,
        grouped_by_target: dict[str, dict[str, Any]],
        progress: dict[str, Any],
    ) -> list[dict[str, Any]]:
        location_state = self._load_location_state(operator_id)
        external_relevance = self._load_external_relevance_evidence(operator_id)
        synthesized: list[dict[str, Any]] = []

        for runtime_target, config in _LOCATION_TARGET_CONFIGS.items():
            current_value = bool(location_state.get(runtime_target, False))
            observation_group = grouped_by_target.get(runtime_target)
            observation_count = int((observation_group or {}).get("observation_count") or 0)
            external_evidence = external_relevance.get(runtime_target)
            evidence_sources: list[str] = []
            suggested_value: bool | None = None
            reasons: list[str] = []

            has_operator_signal = observation_count >= 2
            external_mismatch = False
            if external_evidence is not None:
                usefulness = float(external_evidence.get("usefulness_score") or 0.0)
                sample_size = int(external_evidence.get("sample_size") or 0)
                if sample_size >= 4:
                    if current_value and usefulness <= 0.35:
                        external_mismatch = True
                        suggested_value = False
                    elif not current_value and usefulness >= 0.8:
                        external_mismatch = True
                        suggested_value = True

            if has_operator_signal:
                evidence_sources.append("operator_observation")
                reasons.append("repeated operator notes")
                if not current_value:
                    suggested_value = True

            if external_mismatch:
                evidence_sources.append("external_scan_learning")
                reasons.append("outside-signal usefulness diverges from the current location setting")

            question_ready = False
            if has_operator_signal and not current_value:
                question_ready = True
            elif has_operator_signal and external_mismatch:
                question_ready = True
            elif external_mismatch and progress["stage"] != "cold_start":
                question_ready = True

            if not question_ready:
                continue

            evidence_bundle = {
                "current_location_value": current_value,
                "operator_observation": (
                    {
                        "observation_count": observation_count,
                        "observation_type": observation_group.get("observation_type"),
                        "direction": observation_group.get("direction"),
                        "last_service_date": observation_group.get("last_service_date"),
                    }
                    if observation_group is not None
                    else None
                ),
                "external_scan_learning": external_evidence,
            }
            question_payload = _location_question_payload(
                config=config,
                label=str(config["label"]),
                current_value=current_value,
                suggested_value=suggested_value,
                has_operator_signal=has_operator_signal,
                external_mismatch=external_mismatch,
            )
            justification = _location_justification(
                label=str(config["label"]),
                current_value=current_value,
                has_operator_signal=has_operator_signal,
                external_mismatch=external_mismatch,
                progress_stage=progress["stage"],
            )
            equation_link = equation_link_for_runtime_target(runtime_target)
            confidence = _location_confidence(
                observation_count=observation_count,
                external_sample_size=int((external_evidence or {}).get("sample_size") or 0),
                has_operator_signal=has_operator_signal,
                external_mismatch=external_mismatch,
            )
            synthesized.append(
                {
                    "hypothesis_key": str(config["hypothesis_key"]),
                    "confidence": confidence,
                    "hypothesis_value": {
                        "hypothesis_type": "location_relevance_review",
                        "runtime_target": runtime_target,
                        "question_target": str(config["question_target"]),
                        "promotion_policy": "confirm_then_set_relevance_flag",
                        "agenda_key": str(config["agenda_key"]),
                        "question_kind": "yes_no",
                        "communication_payload": question_payload,
                        "expected_impact": str(config["expected_impact"]),
                        "allowed_runtime_actions": ["set_location_relevance", "request_refresh"],
                        "justification": justification,
                        "equation_terms": equation_link.get("equation_terms", []),
                        "equation_path": equation_link.get("equation_path"),
                        "equation_influence_mode": equation_link.get("influence_mode"),
                        "learning_stage": progress["stage"],
                        "learning_progress": progress,
                        "question_ready": question_ready,
                        "promotion_readiness": "operator_confirmation_required",
                        "evidence_sources": evidence_sources,
                        "evidence_bundle": evidence_bundle,
                        "current_value": current_value,
                        "suggested_value": suggested_value,
                        "agenda_priority": _location_agenda_priority(
                            runtime_target=runtime_target,
                            has_operator_signal=has_operator_signal,
                            external_mismatch=external_mismatch,
                            progress_stage=progress["stage"],
                        ),
                        "observation_count": observation_count,
                        "last_seen_at": (observation_group or {}).get("last_seen_at"),
                        "last_service_date": (observation_group or {}).get("last_service_date"),
                    },
                    "evidence": {
                        "source": "combined_location_evidence",
                        "runtime_target": runtime_target,
                        "current_value": current_value,
                        "observation_count": observation_count,
                        "last_service_date": (observation_group or {}).get("last_service_date"),
                        "external_scan_learning": external_evidence,
                        "evidence_sources": evidence_sources,
                        "learning_stage": progress["stage"],
                    },
                }
            )
        return synthesized

    def _load_learning_progress(self, operator_id: str) -> dict[str, Any]:
        calibration_row = self.db.fetchone(
            """
            SELECT MAX(sample_size)
            FROM confidence_calibration_state
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        evaluation_row = self.db.fetchone(
            """
            SELECT COUNT(*)
            FROM prediction_evaluations
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        calibration_samples = int(calibration_row[0] or 0) if calibration_row is not None else 0
        evaluation_count = int(evaluation_row[0] or 0) if evaluation_row is not None else 0
        effective_samples = max(calibration_samples, evaluation_count)
        thresholds = REGIME_GRADUATION_THRESHOLDS
        if effective_samples >= int(thresholds["mature_min_samples"]):
            stage = "mature"
        elif effective_samples >= int(thresholds["early_learning_min_samples"]):
            stage = "early_learning"
        else:
            stage = "cold_start"
        return {
            "stage": stage,
            "effective_samples": effective_samples,
            "calibration_samples": calibration_samples,
            "evaluation_count": evaluation_count,
            "thresholds": {
                "early_learning_min_samples": int(thresholds["early_learning_min_samples"]),
                "mature_min_samples": int(thresholds["mature_min_samples"]),
            },
        }

    def _load_location_state(self, operator_id: str) -> dict[str, bool]:
        row = self.db.fetchone(
            """
            SELECT transit_relevance, venue_relevance, hotel_travel_relevance
            FROM location_context_profile
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        if row is None:
            return {
                "transit_relevance": False,
                "venue_relevance": False,
                "hotel_travel_relevance": False,
            }
        return {
            "transit_relevance": bool(row[0]),
            "venue_relevance": bool(row[1]),
            "hotel_travel_relevance": bool(row[2]),
        }

    def _load_external_relevance_evidence(self, operator_id: str) -> dict[str, dict[str, Any]]:
        evidence: dict[str, dict[str, Any]] = {}
        for runtime_target, config in _LOCATION_TARGET_CONFIGS.items():
            aliases = tuple(config["dependency_groups"])
            row = self.db.fetchone(
                f"""
                SELECT AVG(usefulness_score), AVG(estimated_effect), MAX(sample_size)
                FROM external_scan_learning_state
                WHERE operator_id = ?
                  AND dependency_group IN ({", ".join("?" for _ in aliases)})
                """,
                [operator_id, *aliases],
            )
            if row is None:
                continue
            sample_size = int(row[2] or 0)
            if sample_size <= 0:
                continue
            evidence[runtime_target] = {
                "dependency_groups": list(aliases),
                "usefulness_score": round(float(row[0] or 0.0), 4),
                "estimated_effect": round(float(row[1] or 0.0), 4),
                "sample_size": sample_size,
            }
        return evidence

    def _log_hypothesis_sync(
        self,
        *,
        operator_id: str,
        current: dict[str, Any] | None,
        hypothesis: dict[str, Any],
    ) -> None:
        current_value = current.get("hypothesis_value") if current else None
        next_value = hypothesis["hypothesis_value"]
        current_count = int((current_value or {}).get("observation_count") or 0) if isinstance(current_value, dict) else 0
        next_count = int(next_value.get("observation_count") or 0)
        if current is None:
            self.memory.log_learning_decision(
                operator_id=operator_id,
                decision_type="hypothesis_created",
                status="created",
                hypothesis_key=hypothesis["hypothesis_key"],
                runtime_target=str(next_value.get("runtime_target") or ""),
                equation_terms=list(next_value.get("equation_terms") or []),
                promotion_policy=str(next_value.get("promotion_policy") or ""),
                rationale=str(next_value.get("justification") or "Created from repeated operator observations."),
                evidence=hypothesis["evidence"],
                action={
                    "question_target": next_value.get("question_target"),
                    "agenda_key": next_value.get("agenda_key"),
                    "allowed_runtime_actions": next_value.get("allowed_runtime_actions", []),
                    "learning_stage": next_value.get("learning_stage"),
                },
                source_ref="hypothesis_synthesis",
            )
            return
        if _should_log_refresh(current_value, next_value, current_count, next_count):
            self.memory.log_learning_decision(
                operator_id=operator_id,
                decision_type="hypothesis_refreshed",
                status="refreshed",
                hypothesis_key=hypothesis["hypothesis_key"],
                runtime_target=str(next_value.get("runtime_target") or ""),
                equation_terms=list(next_value.get("equation_terms") or []),
                promotion_policy=str(next_value.get("promotion_policy") or ""),
                rationale=str(next_value.get("justification") or "New evidence changed the learning posture for this hypothesis."),
                evidence=hypothesis["evidence"],
                action={
                    "previous_observation_count": current_count,
                    "next_observation_count": next_count,
                    "learning_stage": next_value.get("learning_stage"),
                },
                source_ref="hypothesis_synthesis",
            )


def _map_observation_group_to_hypothesis(group: dict[str, Any]) -> dict[str, Any] | None:
    runtime_target = str(group.get("runtime_target") or "")

    if runtime_target == "weather_patio_profile":
        payload = _pattern_question_payload(
            what_is_true_now="I am seeing weather and patio patterns show up more than once in recent notes.",
            why_it_matters="That can change how I read weather-sensitive nights here.",
            one_question="When weather turns bad, does patio demand drop materially, and does good weather create extra covers",
        )
        return {
            "hypothesis_key": "learn::weather_patio_profile",
            "hypothesis_type": "operator_profile_adjustment",
            "runtime_target": "weather_patio_profile",
            "question_target": "weather_patio_profile",
            "promotion_policy": "confirm_then_set_location_profile_hints",
            "agenda_key": "qualitative_pattern::weather_patio",
            "question_kind": "free_text",
            "communication_payload": payload,
            "expected_impact": "Tunes patio exposure and weather sensitivity with confirmed operator context.",
            "allowed_runtime_actions": ["set_location_profile_hints", "request_refresh"],
            "justification": "Repeated weather and patio observations suggest the current weather exposure hints may be off.",
        }
    if runtime_target == "walk_in_mix_review":
        payload = _pattern_question_payload(
            what_is_true_now="Walk-in softness has shown up more than once in recent notes.",
            why_it_matters="That may mean the current demand mix is off on softer nights.",
            one_question="Are walk-ins a larger share of demand on those softer nights than I should assume",
        )
        return {
            "hypothesis_key": "learn::walk_in_mix_review",
            "hypothesis_type": "demand_mix_review",
            "runtime_target": "walk_in_mix_review",
            "question_target": "walk_in_mix_review",
            "promotion_policy": "qualitative_memory_only",
            "agenda_key": "observation_pattern::walk_in_mix",
            "question_kind": "yes_no",
            "communication_payload": payload,
            "expected_impact": "Improves how the agent interprets soft nights and walk-in-heavy misses.",
            "allowed_runtime_actions": [],
            "justification": "Repeated walk-in softness observations suggest the agent should learn more about demand composition.",
        }
    if runtime_target == "reservation_anchor_review":
        payload = _pattern_question_payload(
            what_is_true_now="Reservation falloff or no-shows have come up more than once.",
            why_it_matters="That changes how much weight I should give reservation-heavy nights.",
            one_question="Is that a recurring pattern I should keep in mind when I read the reservation book",
        )
        return {
            "hypothesis_key": "learn::reservation_anchor_review",
            "hypothesis_type": "reservation_pattern_review",
            "runtime_target": "reservation_anchor_review",
            "question_target": "reservation_falloff_pattern",
            "promotion_policy": "qualitative_memory_only",
            "agenda_key": "observation_pattern::reservation_falloff",
            "question_kind": "yes_no",
            "communication_payload": payload,
            "expected_impact": "Improves qualitative explanations around reservation-heavy misses.",
            "allowed_runtime_actions": [],
            "justification": "Repeated reservation falloff observations suggest the agent should ask about recurring no-show behavior.",
        }
    if runtime_target == "service_constraints":
        payload = _pattern_question_payload(
            what_is_true_now="Staffing constraints have shown up in a few recent notes.",
            why_it_matters="That can explain misses and service changes even when demand looks normal.",
            one_question="Is that a recurring issue I should keep in mind when I explain softer nights or service changes",
        )
        return {
            "hypothesis_key": "learn::service_constraints",
            "hypothesis_type": "service_constraint_review",
            "runtime_target": "service_constraints",
            "question_target": "staffing_pattern",
            "promotion_policy": "qualitative_memory_only",
            "agenda_key": "qualitative_pattern::staffing",
            "question_kind": "yes_no",
            "communication_payload": payload,
            "expected_impact": "Improves operational explanations and reminder targeting.",
            "allowed_runtime_actions": [],
            "justification": "Repeated staffing observations suggest there may be a recurring service constraint pattern.",
        }
    if runtime_target == "weather_profile_review":
        payload = _pattern_question_payload(
            what_is_true_now="Recent notes suggest weather may affect demand more directly than the current profile captures.",
            why_it_matters="That would change how I explain softer or stronger weather nights.",
            one_question="What should I understand about your typical weather response on normal dinner service",
        )
        return {
            "hypothesis_key": "learn::weather_profile_review",
            "hypothesis_type": "weather_response_review",
            "runtime_target": "weather_profile_review",
            "question_target": "weather_response_pattern",
            "promotion_policy": "qualitative_memory_only",
            "agenda_key": "observation_pattern::weather_response",
            "question_kind": "free_text",
            "communication_payload": payload,
            "expected_impact": "Improves qualitative weather interpretation without forcing a hard profile change.",
            "allowed_runtime_actions": [],
            "justification": "Repeated weather-linked observations suggest the agent should gather a clearer qualitative weather response pattern.",
        }
    return None


def _merge_observation_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combinable_targets = {
        "weather_patio_profile",
        "weather_profile_review",
        "transit_relevance",
        "venue_relevance",
        "hotel_travel_relevance",
    }
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        runtime_target = str(group.get("runtime_target") or "")
        if runtime_target in combinable_targets:
            merge_key = (runtime_target, "")
        else:
            merge_key = (
                runtime_target,
                f"{group.get('direction') or ''}::{group.get('observation_type') or ''}",
            )
        existing = merged.get(merge_key)
        if existing is None:
            merged[merge_key] = dict(group)
            continue
        existing["observation_count"] = int(existing.get("observation_count") or 0) + int(group.get("observation_count") or 0)
        last_seen = group.get("last_seen_at")
        if last_seen and (existing.get("last_seen_at") is None or last_seen > existing.get("last_seen_at")):
            existing["last_seen_at"] = last_seen
        last_service_date = group.get("last_service_date")
        if last_service_date and (existing.get("last_service_date") is None or last_service_date > existing.get("last_service_date")):
            existing["last_service_date"] = last_service_date
    return list(merged.values())


def _location_confidence(
    *,
    observation_count: int,
    external_sample_size: int,
    has_operator_signal: bool,
    external_mismatch: bool,
) -> str:
    if has_operator_signal and external_mismatch:
        return "high"
    if observation_count >= 3 or external_sample_size >= 8:
        return "high"
    return "medium"


def _location_agenda_priority(
    *,
    runtime_target: str,
    has_operator_signal: bool,
    external_mismatch: bool,
    progress_stage: str,
) -> int:
    priority = equation_priority_for_runtime_target(runtime_target)
    if has_operator_signal and external_mismatch:
        return max(12, priority - 8)
    if has_operator_signal:
        return max(15, priority - 4)
    if external_mismatch and progress_stage == "mature":
        return max(15, priority - 2)
    return priority + 4


def _location_question_payload(
    *,
    config: dict[str, Any],
    label: str,
    current_value: bool,
    suggested_value: bool | None,
    has_operator_signal: bool,
    external_mismatch: bool,
) -> dict[str, Any]:
    if has_operator_signal and external_mismatch:
        what_is_true_now = f"I am still checking how much {label} affects demand here."
        why = f"Your notes and recent outside patterns do not line up yet. {str(config['impact_phrase'])}"
    elif not has_operator_signal and suggested_value is True and not current_value:
        what_is_true_now = f"I am seeing signs that {label} may matter more here than I currently assume."
        why = f"{str(config['impact_phrase'])}"
    elif suggested_value is False and current_value:
        what_is_true_now = f"I am seeing signs that {label} may matter less here than I currently assume."
        why = f"{str(config['impact_phrase'])}"
    else:
        what_is_true_now = f"I am seeing repeated signs that {label} may matter here."
        why = f"{str(config['impact_phrase'])}"

    question = (
        f"Does {label} still really move demand here"
        if suggested_value is False and current_value
        else f"Does {label} really move demand here"
    )

    return _pattern_question_payload(
        what_is_true_now=what_is_true_now,
        why_it_matters=why,
        one_question=question,
        facts={
            "current_value": current_value,
            "suggested_value": suggested_value,
            "has_operator_signal": has_operator_signal,
            "external_mismatch": external_mismatch,
        },
    )


def _pattern_question_payload(
    *,
    what_is_true_now: str,
    why_it_matters: str,
    one_question: str,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return communication_payload(
        category="open_question",
        what_is_true_now=what_is_true_now,
        why_it_matters=why_it_matters,
        one_question=one_question,
        facts=facts,
    )


def _location_justification(
    *,
    label: str,
    current_value: bool,
    has_operator_signal: bool,
    external_mismatch: bool,
    progress_stage: str,
) -> str:
    if has_operator_signal and external_mismatch:
        return (
            f"Repeated operator notes and learned outside-signal usefulness are pulling in different directions for {label}. "
            f"At the current {progress_stage} stage, the agent should ask directly before changing the runtime setting."
        )
    if has_operator_signal and not current_value:
        return (
            f"Repeated operator notes suggest {label} matters, but the current location setting is still off. "
            "A direct operator confirmation would safely align the runtime with what the operator is describing."
        )
    return (
        f"Learned outside-signal usefulness no longer matches the current setting for {label}. "
        f"At the current {progress_stage} stage, this is worth confirming before future context weighting continues."
    )


def _promotion_readiness(mapped: dict[str, Any]) -> str:
    policy = str(mapped.get("promotion_policy") or "")
    if policy == "qualitative_memory_only":
        return "qualitative_only"
    return "operator_confirmation_required"


def _should_log_refresh(
    current_value: Any,
    next_value: dict[str, Any],
    current_count: int,
    next_count: int,
) -> bool:
    if next_count > current_count:
        return True
    if not isinstance(current_value, dict):
        return False
    tracked_fields = (
        "learning_stage",
        "question_ready",
        "agenda_priority",
        "suggested_value",
        "evidence_sources",
    )
    return any(current_value.get(field) != next_value.get(field) for field in tracked_fields)
