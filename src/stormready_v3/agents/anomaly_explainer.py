"""Anomaly Explainer Agent — proposes hypotheses for forecast/actual misses.

See ``policies/anomaly_explainer.md`` for the role contract, taxonomy, and caps.
Integration lives at the end of ``workflows.actuals.record_actual_total_and_update``,
after all seven learning cascades have run. The agent reads the miss context and
writes at most two hypothesis candidates to ``operator_hypothesis_state``; it never
touches forecast state, learning state, or existing hypotheses.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import (
    AgentContext,
    AgentResult,
    AgentRole,
    AgentStatus,
    BaseAgent,
)


_ALLOWED_CONFIDENCE = {"low", "medium", "high"}
_ALLOWED_DEPENDENCY_GROUPS = {
    "weather", "access", "venue", "travel", "walk_in",
    "reservation", "service_state", "civic", "proxy_demand",
    "proxy_event", "proxy_incident", "local_context",
}
_ERROR_THRESHOLD = 0.15


class AnomalyExplainerAgent(BaseAgent):
    role = AgentRole.ANOMALY_EXPLAINER

    def run(self, ctx: AgentContext) -> AgentResult:
        payload = ctx.payload
        try:
            error_pct = float(payload.get("error_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="error_pct not a number",
            )
        if abs(error_pct) < _ERROR_THRESHOLD:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale=f"|error_pct|={abs(error_pct):.3f} below threshold {_ERROR_THRESHOLD}",
            )
        service_state = str(payload.get("service_state", "")).lower()
        if service_state != "normal":
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale=f"service_state={service_state!r} not normal",
            )

        system_prompt = self.policy.system_prompt_body
        user_prompt = self._build_user_prompt(ctx)
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

        hypotheses = self._parse_hypotheses(response, ctx)
        if not hypotheses:
            return AgentResult(
                role=self.role,
                run_id=ctx.run_id,
                status=AgentStatus.EMPTY,
                rationale="no valid hypotheses extracted",
            )
        return AgentResult(
            role=self.role,
            run_id=ctx.run_id,
            status=AgentStatus.OK,
            outputs=hypotheses,
            rationale=f"{len(hypotheses)} hypothesis candidate(s) proposed",
        )

    def _build_user_prompt(self, ctx: AgentContext) -> str:
        p = ctx.payload
        body = {
            "service_date": str(p.get("service_date")) if p.get("service_date") else None,
            "service_window": p.get("service_window"),
            "service_state": p.get("service_state"),
            "error_pct": p.get("error_pct"),
            "forecast_expected": p.get("forecast_expected"),
            "forecast_interval": p.get("forecast_interval"),
            "actual_total": p.get("actual_total"),
            "forecast_digest": p.get("forecast_digest"),
            "recent_notes": p.get("recent_notes") or [],
            "open_hypotheses": p.get("open_hypotheses") or [],
        }
        return json.dumps(body, default=str, ensure_ascii=False, indent=2)

    def _parse_hypotheses(
        self, response: dict[str, Any], ctx: AgentContext
    ) -> list[dict[str, Any]]:
        items = response.get("hypotheses")
        if not isinstance(items, list):
            return []
        existing_keys = {
            h.get("hypothesis_key")
            for h in (ctx.payload.get("open_hypotheses") or [])
            if isinstance(h, dict)
        }
        seen_keys: set[str] = set()
        valid: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            category = item.get("category")
            if category not in self.policy.allowed_categories:
                continue
            proposition = str(item.get("proposition", "")).strip()
            if not proposition or len(proposition) > 500:
                continue
            evidence = str(item.get("evidence", "")).strip()
            if not evidence:
                continue
            confidence = item.get("confidence", "low")
            if confidence not in _ALLOWED_CONFIDENCE:
                confidence = "low"
            dependency_group = item.get("dependency_group", "local_context")
            if dependency_group not in _ALLOWED_DEPENDENCY_GROUPS:
                dependency_group = "local_context"
            key = _slugify(proposition)
            if not key or key in seen_keys or key in existing_keys:
                continue
            seen_keys.add(key)
            valid.append({
                "hypothesis_key": key,
                "category": category,
                "proposition": proposition,
                "evidence": evidence,
                "confidence": confidence,
                "dependency_group": dependency_group,
                "origin": "anomaly_explainer",
                "trigger_error_pct": ctx.payload.get("error_pct"),
                "trigger_run_id": ctx.payload.get("prediction_run_id"),
            })
            if len(valid) >= self.policy.max_outputs_per_run:
                break
        return valid


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return cleaned[:80]


__all__ = ["AnomalyExplainerAgent"]
