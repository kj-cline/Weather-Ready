from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stormready_v3.agents.tools import ToolExecutor, ToolResult
from stormready_v3.conversation.equation_links import equation_link_for_runtime_target
from stormready_v3.conversation.memory import ConversationMemoryService
from stormready_v3.conversation.profile_adjustments import infer_weather_patio_adjustment
from stormready_v3.operator_text import communication_payload
from stormready_v3.storage.db import Database


@dataclass(slots=True)
class PromotionResolution:
    status: str
    rationale: str
    communication_payload: dict[str, Any] = field(default_factory=dict)
    tool_results: list[ToolResult] = field(default_factory=list)
    fact_updates: list[dict[str, Any]] = field(default_factory=list)
    runtime_target: str | None = None
    promotion_policy: str | None = None
    action: dict[str, Any] = field(default_factory=dict)


def _promotion_response_payload(
    *,
    why_it_matters: str,
    what_i_need_from_you: str | None = None,
    what_is_still_uncertain: str | None = None,
) -> dict[str, Any]:
    return communication_payload(
        category="learning_resolution",
        why_it_matters=why_it_matters,
        what_i_need_from_you=what_i_need_from_you,
        what_is_still_uncertain=what_is_still_uncertain,
    )
def _target_label(target_fact_key: str) -> str:
    labels = {
        "transit_relevance": "nearby transit or access",
        "venue_relevance": "nearby venues or event traffic",
        "hotel_travel_relevance": "nearby hotels and travel demand",
    }
    return labels.get(target_fact_key, target_fact_key.replace("_", " "))


class LearningPromotionService:
    def __init__(self, db: Database, executor: ToolExecutor) -> None:
        self.db = db
        self.executor = executor
        self.memory = ConversationMemoryService(db)

    def resolve_yes_no(
        self,
        *,
        operator_id: str,
        agenda_item: dict[str, Any],
        answer: bool,
    ) -> PromotionResolution:
        hypothesis = self._load_hypothesis(operator_id, agenda_item)
        target_fact_key = str(agenda_item.get("target_fact_key") or "")
        runtime_target = self._runtime_target(hypothesis, agenda_item)
        equation_terms = self._equation_terms(hypothesis, runtime_target)
        promotion_policy = self._promotion_policy(hypothesis)
        tool_results: list[ToolResult] = []
        action: dict[str, Any] = {"answer": answer}
        if not self._is_runtime_target_promotable(runtime_target, promotion_policy):
            fact_key = target_fact_key or f"agenda_answer::{agenda_item.get('agenda_key')}"
            fact_updates = [
                {
                    "fact_key": fact_key,
                    "fact_value": answer,
                    "confidence": "high",
                    "provenance": "operator_confirmed",
                    "source_ref": f"learning_agenda::{agenda_item.get('agenda_key')}",
                }
            ]
            rationale = "The answer was recorded, but there was no explicit runtime target to update safely."
            self.memory.log_learning_decision(
                operator_id=operator_id,
                decision_type="promotion_skipped",
                status="no_runtime_target",
                hypothesis_key=agenda_item.get("hypothesis_key"),
                agenda_key=agenda_item.get("agenda_key"),
                runtime_target=runtime_target,
                equation_terms=equation_terms,
                promotion_policy=promotion_policy,
                rationale=rationale,
                evidence=hypothesis.get("evidence") or {},
                action=action,
                source_ref=f"learning_agenda::{agenda_item.get('agenda_key')}",
            )
            payload = _promotion_response_payload(
                why_it_matters="I recorded that and will use it in future forecasts.",
            )
            return PromotionResolution(
                status="recorded_only",
                rationale=rationale,
                communication_payload=payload,
                runtime_target=runtime_target,
                promotion_policy=promotion_policy,
                action=action,
                fact_updates=fact_updates,
            )

        if target_fact_key in {"transit_relevance", "venue_relevance", "hotel_travel_relevance"}:
            update_result = self.executor.execute(operator_id, "set_location_relevance", {target_fact_key: answer})
            tool_results.append(update_result)
            action["set_location_relevance"] = {target_fact_key: answer}
            if update_result.success:
                refresh_result = self.executor.execute(
                    operator_id,
                    "request_refresh",
                    {"reason": f"agenda_resolution::{target_fact_key}"},
                )
                tool_results.append(refresh_result)
                action["request_refresh"] = refresh_result.success
        runtime_applied = all(result.success for result in tool_results) if tool_results else False

        fact_key = target_fact_key or f"agenda_answer::{agenda_item.get('agenda_key')}"
        fact_updates = [
            {
                "fact_key": fact_key,
                "fact_value": answer,
                "confidence": "high",
                "provenance": "operator_confirmed",
                "source_ref": f"learning_agenda::{agenda_item.get('agenda_key')}",
            }
        ]
        rationale = str(
            (hypothesis.get("hypothesis_value") or {}).get("justification")
            or agenda_item.get("rationale")
            or "Operator confirmation resolved this relevance decision."
        )
        self.memory.log_learning_decision(
            operator_id=operator_id,
            decision_type="promotion_applied",
            status="applied" if runtime_applied else "recorded_only",
            hypothesis_key=agenda_item.get("hypothesis_key"),
            agenda_key=agenda_item.get("agenda_key"),
            runtime_target=runtime_target,
            equation_terms=equation_terms,
            promotion_policy=promotion_policy,
            rationale=rationale,
            evidence=hypothesis.get("evidence") or {},
            action=action,
            source_ref=f"learning_agenda::{agenda_item.get('agenda_key')}",
        )
        target_label = _target_label(target_fact_key or fact_key)
        payload = _promotion_response_payload(
            why_it_matters=(
                f"I will factor {target_label} into future forecasts."
                if runtime_applied and answer
                else f"I will not lean much on {target_label} in future forecasts."
                if runtime_applied and not answer
                else f"I recorded that about {target_label}, but I could not update the forecast setup yet."
            ),
        )
        return PromotionResolution(
            status="applied" if runtime_applied else "recorded_only",
            rationale=rationale,
            communication_payload=payload,
            tool_results=tool_results,
            fact_updates=fact_updates,
            runtime_target=runtime_target,
            promotion_policy=promotion_policy,
            action=action,
        )

    def resolve_free_text(
        self,
        *,
        operator_id: str,
        agenda_item: dict[str, Any],
        message: str,
    ) -> PromotionResolution:
        hypothesis = self._load_hypothesis(operator_id, agenda_item)
        runtime_target = self._runtime_target(hypothesis, agenda_item)
        equation_terms = self._equation_terms(hypothesis, runtime_target)
        promotion_policy = self._promotion_policy(hypothesis)
        if not self._is_runtime_target_promotable(runtime_target, promotion_policy):
            rationale = "The note was captured, but there was no explicit runtime target to update safely."
            self.memory.log_learning_decision(
                operator_id=operator_id,
                decision_type="promotion_skipped",
                status="no_runtime_target",
                hypothesis_key=agenda_item.get("hypothesis_key"),
                agenda_key=agenda_item.get("agenda_key"),
                runtime_target=runtime_target,
                equation_terms=equation_terms,
                promotion_policy=promotion_policy,
                rationale=rationale,
                evidence=hypothesis.get("evidence") or {},
                action={"message": message},
                source_ref=f"learning_agenda::{agenda_item.get('agenda_key')}",
            )
            payload = _promotion_response_payload(
                why_it_matters="I recorded that context and will use it in future forecasts.",
            )
            return PromotionResolution(
                status="recorded_only",
                rationale=rationale,
                communication_payload=payload,
                runtime_target=runtime_target,
                promotion_policy=promotion_policy,
                action={"message": message},
            )

        if runtime_target == "weather_patio_profile" or promotion_policy == "confirm_then_set_location_profile_hints":
            operator_profile = self.executor.operators.load_operator_profile(operator_id)
            current_context = self.executor.operators.load_location_context(operator_id)
            adjustment = infer_weather_patio_adjustment(
                message,
                current_context=current_context,
                operator_profile=operator_profile,
            )
            if adjustment is None:
                rationale = "The operator reply added context, but it was not specific enough to justify a profile-hint update."
                self.memory.log_learning_decision(
                    operator_id=operator_id,
                    decision_type="promotion_skipped",
                    status="not_specific_enough",
                    hypothesis_key=agenda_item.get("hypothesis_key"),
                    agenda_key=agenda_item.get("agenda_key"),
                    runtime_target=runtime_target,
                    equation_terms=equation_terms,
                    promotion_policy=promotion_policy,
                    rationale=rationale,
                    evidence=hypothesis.get("evidence") or {},
                    action={"message": message},
                    source_ref=f"learning_agenda::{agenda_item.get('agenda_key')}",
                )
                payload = _promotion_response_payload(
                    why_it_matters="I recorded that note, but I need a little more detail before I change future forecasts.",
                )
                return PromotionResolution(
                    status="recorded_only",
                    rationale=rationale,
                    communication_payload=payload,
                    runtime_target=runtime_target,
                    promotion_policy=promotion_policy,
                    action={"message": message},
                )

            hint_args: dict[str, Any] = {}
            if adjustment.patio_sensitivity_hint is not None:
                hint_args["patio_sensitivity_hint"] = adjustment.patio_sensitivity_hint
            if adjustment.weather_sensitivity_hint is not None:
                hint_args["weather_sensitivity_hint"] = adjustment.weather_sensitivity_hint

            tool_results: list[ToolResult] = []
            action: dict[str, Any] = {"message": message, "hint_args": hint_args}
            if hint_args:
                hint_update = self.executor.execute(operator_id, "set_location_profile_hints", hint_args)
                tool_results.append(hint_update)
                action["set_location_profile_hints"] = hint_update.success
                if hint_update.success:
                    refresh_result = self.executor.execute(
                        operator_id,
                        "request_refresh",
                        {"reason": "agenda_resolution::weather_patio_profile"},
                    )
                    tool_results.append(refresh_result)
                    action["request_refresh"] = refresh_result.success
            runtime_applied = any(result.tool_name == "set_location_profile_hints" and result.success for result in tool_results)
            refresh_ok = all(result.success for result in tool_results)

            fact_updates = [
                {
                    "fact_key": "confirmed_profile::weather_patio_behavior",
                    "fact_value": adjustment.fact_value,
                    "confidence": adjustment.confidence,
                    "provenance": "operator_confirmed",
                    "source_ref": f"learning_agenda::{agenda_item.get('agenda_key')}",
                }
            ]
            rationale = str(
                (hypothesis.get("hypothesis_value") or {}).get("justification")
                or adjustment.summary
                or "Confirmed operator answer justified a bounded weather/patio profile update."
            )
            self.memory.log_learning_decision(
                operator_id=operator_id,
                decision_type="promotion_applied",
                status="applied" if refresh_ok else ("runtime_updated_refresh_failed" if runtime_applied else "recorded_only"),
                hypothesis_key=agenda_item.get("hypothesis_key"),
                agenda_key=agenda_item.get("agenda_key"),
                runtime_target=runtime_target,
                equation_terms=equation_terms,
                promotion_policy=promotion_policy,
                rationale=rationale,
                evidence=hypothesis.get("evidence") or {},
                action=action,
                source_ref=f"learning_agenda::{agenda_item.get('agenda_key')}",
            )
            payload = _promotion_response_payload(
                why_it_matters=(
                    f"I will carry this forward: {adjustment.summary}. I refreshed the forecast too."
                    if runtime_applied and refresh_ok
                    else f"I will carry this forward: {adjustment.summary}. I could not refresh the forecast yet."
                    if runtime_applied and not refresh_ok
                    else "I recorded that context, but I could not update the forecast setup yet."
                ),
            )
            return PromotionResolution(
                status="applied" if refresh_ok else ("runtime_updated_refresh_failed" if runtime_applied else "recorded_only"),
                rationale=rationale,
                communication_payload=payload,
                tool_results=tool_results,
                fact_updates=fact_updates,
                runtime_target=runtime_target,
                promotion_policy=promotion_policy,
                action=action,
            )

        rationale = str(
            (hypothesis.get("hypothesis_value") or {}).get("justification")
            or agenda_item.get("rationale")
            or "This answer is being kept as qualitative learning only."
        )
        self.memory.log_learning_decision(
            operator_id=operator_id,
            decision_type="promotion_deferred",
            status="qualitative_only",
            hypothesis_key=agenda_item.get("hypothesis_key"),
            agenda_key=agenda_item.get("agenda_key"),
            runtime_target=runtime_target,
            equation_terms=equation_terms,
            promotion_policy=promotion_policy,
            rationale=rationale,
            evidence=hypothesis.get("evidence") or {},
            action={"message": message},
            source_ref=f"learning_agenda::{agenda_item.get('agenda_key')}",
        )
        payload = _promotion_response_payload(
            why_it_matters="I recorded that context and will use it in future forecasts.",
        )
        return PromotionResolution(
            status="recorded_only",
            rationale=rationale,
            communication_payload=payload,
            runtime_target=runtime_target,
            promotion_policy=promotion_policy,
            action={"message": message},
        )

    def _load_hypothesis(self, operator_id: str, agenda_item: dict[str, Any]) -> dict[str, Any]:
        hypothesis_key = str(agenda_item.get("hypothesis_key") or "")
        if not hypothesis_key:
            return {}
        return self.memory.get_hypothesis(operator_id, hypothesis_key) or {}

    @staticmethod
    def _promotion_policy(hypothesis: dict[str, Any]) -> str | None:
        value = hypothesis.get("hypothesis_value") or {}
        if isinstance(value, dict):
            policy = value.get("promotion_policy")
            return str(policy) if policy is not None else None
        return None

    @staticmethod
    def _runtime_target(hypothesis: dict[str, Any], agenda_item: dict[str, Any]) -> str | None:
        value = hypothesis.get("hypothesis_value") or {}
        if isinstance(value, dict) and value.get("runtime_target") is not None:
            return str(value.get("runtime_target"))
        target_fact_key = agenda_item.get("target_fact_key")
        return str(target_fact_key) if target_fact_key is not None else None

    @staticmethod
    def _equation_terms(hypothesis: dict[str, Any], runtime_target: str | None) -> list[str]:
        value = hypothesis.get("hypothesis_value") or {}
        if isinstance(value, dict) and value.get("equation_terms") is not None:
            return list(value.get("equation_terms") or [])
        return list(equation_link_for_runtime_target(runtime_target).get("equation_terms", []))

    @staticmethod
    def _is_runtime_target_promotable(runtime_target: str | None, promotion_policy: str | None) -> bool:
        if not runtime_target:
            return False
        allowed_targets = {
            "transit_relevance",
            "venue_relevance",
            "hotel_travel_relevance",
            "weather_patio_profile",
        }
        if runtime_target not in allowed_targets:
            return False
        if runtime_target == "weather_patio_profile":
            return promotion_policy == "confirm_then_set_location_profile_hints"
        return True
