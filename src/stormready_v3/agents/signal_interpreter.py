"""Signal Interpreter Agent — extracts typed signals from unstructured narrative sources.

See ``policies/signal_interpreter.md`` for the full role contract, taxonomy, and caps.
Integration lives inside ``DeterministicOrchestrator.refresh_forecast_for_date``: the
agent runs after ``fetch_source_payloads`` and before ``normalize_source_payloads``,
converting qualifying payloads' narrative text into additional entries that flow
through the existing normalization pipeline.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .base import (
    AgentContext,
    AgentResult,
    AgentRole,
    AgentStatus,
    BaseAgent,
)


_ALLOWED_DEPENDENCY_GROUPS = {
    "weather", "access", "venue", "travel", "walk_in",
    "reservation", "service_state", "civic", "proxy_demand",
    "proxy_event", "proxy_incident", "local_context",
}
_ALLOWED_DIRECTIONS = {"up", "down", "neutral"}
_ALLOWED_ROLES = {
    "numeric_mover", "confidence_mover", "posture_mover", "service_state_modifier"
}
_ALLOWED_SOURCE_BUCKETS = {"curated_local", "broad_proxy"}
_ALWAYS_TIER2_CATEGORIES = {"narrative_weather_context", "novel_unmapped"}


class SignalInterpreterAgent(BaseAgent):
    role = AgentRole.SIGNAL_INTERPRETER

    def run(self, ctx: AgentContext) -> AgentResult:
        raw_payloads = ctx.payload.get("payloads") or []
        eligible = self._filter_eligible(raw_payloads)
        if not eligible:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="no eligible payloads with narrative_text",
            )

        system_prompt = self.policy.system_prompt_body
        user_prompt = self._build_user_prompt(ctx, eligible)
        try:
            response = self.provider.structured_json_call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=self.policy.max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.FAILED,
                error=f"provider raised {type(exc).__name__}: {exc}",
            )
        if response is None:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.FAILED,
                error="provider returned None",
            )

        parsed = self._parse_signals(response, ctx)
        if not parsed:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="no valid signals extracted from provider response",
            )

        classified = self._classify_tiers(parsed)
        tier1_count = sum(1 for s in classified if s["status"] == "observed")
        tier2_count = sum(1 for s in classified if s["status"] == "proposed")
        return AgentResult(
            role=self.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=classified,
            rationale=f"{len(classified)} signal(s): {tier1_count} tier1, {tier2_count} tier2",
        )

    def _filter_eligible(
        self, raw_payloads: Iterable[Any]
    ) -> list[dict[str, Any]]:
        forbidden = set(self.policy.forbidden_source_classes)
        eligible: list[dict[str, Any]] = []
        for p in raw_payloads:
            if not isinstance(p, dict):
                continue
            if p.get("source_class") in forbidden:
                continue
            inner = p.get("payload") or {}
            if not isinstance(inner, dict):
                continue
            narrative = inner.get("narrative_text")
            if not narrative or not str(narrative).strip():
                continue
            eligible.append(p)
        return eligible

    def _build_user_prompt(
        self, ctx: AgentContext, payloads: list[dict[str, Any]]
    ) -> str:
        operator_context = ctx.payload.get("operator_context") or {}
        service_date = ctx.payload.get("service_date")
        service_window = ctx.payload.get("service_window")
        compact = [
            {
                "source_name": p.get("source_name"),
                "source_class": p.get("source_class"),
                "source_bucket": p.get("source_bucket"),
                "narrative_text": (p.get("payload") or {}).get("narrative_text"),
            }
            for p in payloads
        ]
        body = {
            "operator_context": operator_context,
            "service_date": str(service_date) if service_date else None,
            "service_window": service_window,
            "payloads": compact,
        }
        return json.dumps(body, default=str, ensure_ascii=False, indent=2)

    def _parse_signals(
        self, response: dict[str, Any], ctx: AgentContext
    ) -> list[dict[str, Any]]:
        items = response.get("signals")
        if not isinstance(items, list):
            return []
        fallback_service_date = ctx.payload.get("service_date")
        valid: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            category = item.get("category")
            if category not in self.policy.allowed_categories:
                continue
            dependency_group = item.get("dependency_group")
            if dependency_group not in _ALLOWED_DEPENDENCY_GROUPS:
                continue
            agent_role_field = item.get("role", "numeric_mover")
            if agent_role_field not in _ALLOWED_ROLES:
                continue
            direction = item.get("direction")
            if direction not in _ALLOWED_DIRECTIONS:
                continue
            try:
                strength = float(item.get("strength", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if strength < 0.0 or strength > 1.0:
                continue

            # Hard constraint: narrative weather is strictly posture-only.
            if category == "narrative_weather_context":
                if agent_role_field != "confidence_mover" or strength != 0.0:
                    continue
            # Hard constraint: nothing else may produce numeric weather movers.
            elif dependency_group == "weather" and agent_role_field == "numeric_mover":
                continue

            source_bucket = item.get("source_bucket", "broad_proxy")
            if source_bucket not in _ALLOWED_SOURCE_BUCKETS:
                source_bucket = "broad_proxy"

            service_date = item.get("service_date") or fallback_service_date
            valid.append({
                "category": category,
                "dependency_group": dependency_group,
                "role": agent_role_field,
                "direction": direction,
                "strength": strength,
                "service_date": str(service_date) if service_date else None,
                "source_name": str(item.get("source_name", "signal_interpreter"))[:80],
                "source_bucket": source_bucket,
                "rationale": str(item.get("rationale", ""))[:500],
            })
            if len(valid) >= self.policy.max_outputs_per_run:
                break
        return valid

    def _classify_tiers(
        self, signals: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        classified: list[dict[str, Any]] = []
        tier1_total = 0.0
        for sig in signals:
            reasons: list[str] = []
            if sig["category"] in _ALWAYS_TIER2_CATEGORIES:
                reasons.append(
                    f"category {sig['category']} always requires operator review"
                )
            if sig["strength"] > self.policy.tier1_max_strength_per_signal:
                reasons.append(
                    f"strength {sig['strength']:.3f} exceeds per-signal cap "
                    f"{self.policy.tier1_max_strength_per_signal:.3f}"
                )
            if not reasons and (
                tier1_total + sig["strength"] > self.policy.tier1_max_strength_total
            ):
                reasons.append(
                    f"would exceed per-run total strength cap "
                    f"{self.policy.tier1_max_strength_total:.3f}"
                )
            if reasons:
                sig["status"] = "proposed"
                sig["tier2_reasons"] = reasons
            else:
                sig["status"] = "observed"
                sig["tier2_reasons"] = []
                tier1_total += sig["strength"]
            classified.append(sig)
        return classified


__all__ = ["SignalInterpreterAgent"]
