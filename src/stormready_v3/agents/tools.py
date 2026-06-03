"""Tool definitions and executors for the unified StormReady agent.

Each tool maps operator language to system contracts. Tools are grouped by phase:
- SETUP tools: create/update operator profile, set fields, check readiness
- DATA tools: interpret uploaded files, import historical data
- OPERATIONS tools: get forecast, explain changes, capture notes, refresh forecasts
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from stormready_v3.agents.base import AgentDispatcher
from stormready_v3.ai.contracts import AgentModelProvider
from stormready_v3.domain.enums import DemandMix, NeighborhoodType, ServiceWindow
from stormready_v3.domain.models import LocationContextProfile, OperatorProfile
from stormready_v3.conversation.equation_links import prediction_equation_contract, summarize_equation_learning_state
from stormready_v3.conversation.memory import ConversationMemoryService
from stormready_v3.operator_text import forecast_headline
from stormready_v3.setup.readiness import summarize_setup_readiness
from stormready_v3.setup.service import SetupRequest, SetupService
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository


# ---------------------------------------------------------------------------
# Tool result
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ToolResult:
    tool_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Executes agent tool calls against the real services."""

    def __init__(
        self,
        db: Database,
        provider: AgentModelProvider | None = None,
        *,
        agent_dispatcher: AgentDispatcher | None = None,
        reference_date: date | None = None,
        defer_profile_enrichment: bool = False,
    ) -> None:
        self.db = db
        self.provider = provider
        self.agent_dispatcher = agent_dispatcher
        self.reference_date = reference_date
        self.defer_profile_enrichment = defer_profile_enrichment
        self.operators = OperatorRepository(db)

    def set_reference_date(self, reference_date: date | None) -> None:
        self.reference_date = reference_date

    def _effective_reference_date(self) -> date:
        return self.reference_date or date.today()

    def _load_location_context_for_update(
        self,
        operator_id: str,
        *,
        profile: OperatorProfile,
    ) -> LocationContextProfile:
        current = self.operators.load_location_context(operator_id)
        if current is not None:
            return current
        return _load_empty_location_context(
            operator_id,
            neighborhood_type=profile.neighborhood_type,
        )

    def execute(self, operator_id: str | None, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_exec_{tool_name}", None)
        if handler is None:
            return ToolResult(tool_name=tool_name, success=False, message="I could not complete that step from chat.")
        try:
            return handler(operator_id, arguments)
        except Exception as exc:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                message=f"I hit an error while trying to complete that step: {exc}",
            )

    def _exec_update_profile(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        from stormready_v3.setup.geocoding import CensusGeocoder

        # Build or update the profile from provided fields
        existing_profile = self.operators.load_operator_profile(operator_id) if operator_id else None

        restaurant_name = args.get("restaurant_name") or (existing_profile.restaurant_name if existing_profile else None)
        canonical_address = args.get("canonical_address") or (existing_profile.canonical_address if existing_profile else None)

        if not restaurant_name:
            return ToolResult(tool_name="update_profile", success=False, message="I still need the restaurant name.")
        if not canonical_address:
            return ToolResult(tool_name="update_profile", success=False, message="I still need the street address.")

        # Resolve operator_id from name if new
        if operator_id is None:
            operator_id = _slugify(restaurant_name)
            existing_ids = set(self.operators.list_active_operator_ids())
            operator_id = _next_available_id(operator_id, existing_ids)

        # Merge with existing profile defaults
        neighborhood_str = args.get("neighborhood_type")
        neighborhood = NeighborhoodType(neighborhood_str) if neighborhood_str else (
            existing_profile.neighborhood_type if existing_profile else NeighborhoodType.MIXED_URBAN
        )
        demand_mix_str = args.get("demand_mix")
        demand_mix = DemandMix(demand_mix_str) if demand_mix_str else (
            existing_profile.demand_mix if existing_profile else DemandMix.MIXED
        )
        patio_enabled = args.get("patio_enabled", existing_profile.patio_enabled if existing_profile else False)
        patio_capacity = args.get("patio_seat_capacity") or (
            existing_profile.patio_seat_capacity if existing_profile else None
        )
        patio_season = args.get("patio_season_mode") or (
            existing_profile.patio_season_mode if existing_profile else None
        )

        baselines = args.get("weekly_baselines")
        weekly_baseline_source_type = str(args.get("weekly_baseline_source_type") or "operator_setup")

        service = SetupService(
            self.operators,
            geocoder=CensusGeocoder.with_default_client(),
            db=self.db,
            ai_provider=self.provider,
        )
        profile = service.create_or_update_operator(
            SetupRequest(
                operator_id=operator_id,
                restaurant_name=restaurant_name,
                canonical_address=canonical_address,
                city=args.get("city") or (existing_profile.city if existing_profile else None),
                timezone=args.get("timezone") or (existing_profile.timezone if existing_profile else "America/New_York"),
                neighborhood_type=neighborhood,
                demand_mix=demand_mix,
                indoor_seat_capacity=existing_profile.indoor_seat_capacity if existing_profile else None,
                patio_enabled=bool(patio_enabled),
                patio_seat_capacity=int(patio_capacity) if patio_enabled and patio_capacity else None,
                patio_season_mode=patio_season,
                weekly_baselines=baselines,
                weekly_baseline_source_type=weekly_baseline_source_type,
            ),
            run_enrichment=not self.defer_profile_enrichment,
        )
        return ToolResult(
            tool_name="update_profile",
            success=True,
            data={
                "operator_id": profile.operator_id,
                "onboarding_state": profile.onboarding_state.value,
                "restaurant_name": profile.restaurant_name,
                "neighborhood_type": profile.neighborhood_type.value,
                "demand_mix": profile.demand_mix.value,
                "patio_enabled": profile.patio_enabled,
            },
            message=f"I saved the profile for {profile.restaurant_name}.",
        )

    def _exec_set_location_relevance(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="set_location_relevance", success=False, message="Set up the restaurant profile first.")
        profile = self.operators.load_operator_profile(operator_id)
        if profile is None:
            return ToolResult(tool_name="set_location_relevance", success=False, message="I could not find that restaurant profile.")
        updates: dict[str, Any] = {}
        for flag in ("transit_relevance", "venue_relevance", "hotel_travel_relevance"):
            if flag in args:
                updates[flag] = bool(args[flag])
        if not updates:
            return ToolResult(tool_name="set_location_relevance", success=False, message="I did not get any location relevance updates.")
        current = self._load_location_context_for_update(operator_id, profile=profile)
        self.operators.upsert_location_context(
            LocationContextProfile(
                operator_id=operator_id,
                neighborhood_archetype=current.neighborhood_archetype,
                commuter_intensity=current.commuter_intensity,
                residential_intensity=current.residential_intensity,
                transit_relevance=updates.get("transit_relevance", current.transit_relevance),
                venue_relevance=updates.get("venue_relevance", current.venue_relevance),
                hotel_travel_relevance=updates.get("hotel_travel_relevance", current.hotel_travel_relevance),
                patio_sensitivity_hint=current.patio_sensitivity_hint,
                weather_sensitivity_hint=current.weather_sensitivity_hint,
                demand_volatility_hint=current.demand_volatility_hint,
            )
        )
        return ToolResult(
            tool_name="set_location_relevance",
            success=True,
            data=updates,
            message="I updated the location context.",
        )

    def _exec_set_location_profile_hints(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="set_location_profile_hints", success=False, message="Set up the restaurant profile first.")
        profile = self.operators.load_operator_profile(operator_id)
        if profile is None:
            return ToolResult(tool_name="set_location_profile_hints", success=False, message="I could not find that restaurant profile.")
        current = self._load_location_context_for_update(operator_id, profile=profile)

        updates: dict[str, float] = {}
        if args.get("patio_sensitivity_hint") is not None:
            updates["patio_sensitivity_hint"] = max(0.0, min(2.0, float(args["patio_sensitivity_hint"])))
        if args.get("weather_sensitivity_hint") is not None:
            updates["weather_sensitivity_hint"] = max(0.7, min(1.5, float(args["weather_sensitivity_hint"])))
        if not updates:
            return ToolResult(tool_name="set_location_profile_hints", success=False, message="I did not get any location profile hints to save.")

        self.operators.upsert_location_context(
            LocationContextProfile(
                operator_id=operator_id,
                neighborhood_archetype=current.neighborhood_archetype,
                commuter_intensity=current.commuter_intensity,
                residential_intensity=current.residential_intensity,
                transit_relevance=current.transit_relevance,
                venue_relevance=current.venue_relevance,
                hotel_travel_relevance=current.hotel_travel_relevance,
                patio_sensitivity_hint=updates.get("patio_sensitivity_hint", current.patio_sensitivity_hint),
                weather_sensitivity_hint=updates.get("weather_sensitivity_hint", current.weather_sensitivity_hint),
                demand_volatility_hint=current.demand_volatility_hint,
            )
        )
        return ToolResult(
            tool_name="set_location_profile_hints",
            success=True,
            data=updates,
            message="I updated the location profile hints.",
        )

    def _exec_check_readiness(self, operator_id: str | None, _args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(
                tool_name="check_readiness",
                success=True,
                data={"forecast_ready": False},
                message="No restaurant profile exists yet. Tell me your restaurant name and address to get started.",
            )
        profile = self.operators.load_operator_profile(operator_id)
        if profile is None:
            return ToolResult(tool_name="check_readiness", success=False, message="I could not find that restaurant profile.")

        baseline_row = self.db.fetchone(
            "SELECT COUNT(*) FROM operator_weekly_baselines WHERE operator_id = ? AND baseline_total_covers > 0",
            [operator_id],
        )
        has_baselines = baseline_row is not None and baseline_row[0] > 0

        summary = summarize_setup_readiness(profile, primary_window_has_baseline=has_baselines)
        return ToolResult(
            tool_name="check_readiness",
            success=True,
            data={
                "onboarding_state": summary.onboarding_state.value,
                "prediction_case": summary.prediction_case.value,
                "forecast_ready": summary.forecast_ready,
                "has_address": summary.has_address,
                "has_baselines": summary.has_baselines,
                "has_patio": summary.has_patio,
                "improvements": summary.improvements,
            },
            message="I checked the setup state.",
        )

    def _exec_get_forecast(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="get_forecast", success=False, message="I do not have a restaurant profile set up yet.")
        from stormready_v3.config.settings import ACTIONABLE_HORIZON_DAYS
        days = args.get("days_ahead", ACTIONABLE_HORIZON_DAYS)
        ref = date.fromisoformat(args["service_date"]) if args.get("service_date") else self._effective_reference_date()
        end = date.fromordinal(ref.toordinal() + max(0, days - 1))
        rows = self.db.fetchall(
            """
            SELECT p.service_date, p.service_window, p.forecast_expected, p.forecast_low, p.forecast_high,
                   p.confidence_tier, p.posture, p.service_state, p.top_drivers_json, e.digest_json
            FROM published_forecast_state p
            LEFT JOIN engine_digest e ON e.prediction_run_id = p.source_prediction_run_id
            WHERE p.operator_id = ? AND p.service_date BETWEEN ? AND ?
            ORDER BY p.service_date, p.service_window
            """,
            [operator_id, ref, end],
        )
        cards = []
        for row in rows:
            import json as _json
            service_window = str(row[1])
            posture = str(row[6])
            service_state = str(row[7])
            digest = _json.loads(row[9]) if row[9] else {}
            baseline = digest.get("baseline")
            total_pct = digest.get("total_pct")
            vs_usual_pct = int(round(float(total_pct) * 100)) if total_pct not in {None, ""} else None
            vs_usual_covers = (
                int(row[2]) - int(float(baseline))
                if baseline not in {None, ""} and row[2] is not None
                else None
            )
            cards.append({
                "service_date": str(row[0]),
                "service_window": service_window,
                "forecast_expected": row[2],
                "forecast_low": row[3],
                "forecast_high": row[4],
                "confidence_tier": row[5],
                "posture": posture,
                "service_state": service_state,
                "headline": forecast_headline(
                    service_window=service_window,
                    posture=posture,
                    service_state=service_state,
                ),
                "top_drivers": _json.loads(row[8]) if row[8] else [],
                "baseline": baseline,
                "vs_usual_pct": vs_usual_pct,
                "vs_usual_covers": vs_usual_covers,
                "reference_model": digest.get("reference_model"),
            })
        if not cards:
            return ToolResult(tool_name="get_forecast", success=True, data={"cards": []},
                              message="I do not have a published forecast for that period yet.")
        return ToolResult(tool_name="get_forecast", success=True, data={"cards": cards},
                          message=f"Found {len(cards)} forecast(s).")

    def _exec_explain_forecast(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="explain_forecast", success=False, message="I do not have a restaurant profile set up yet.")
        service_date = args.get("service_date")
        if not service_date:
            return ToolResult(tool_name="explain_forecast", success=False, message="I still need the service date.")
        row = self.db.fetchone(
            """
            SELECT p.forecast_expected, p.forecast_low, p.forecast_high, p.confidence_tier, p.posture,
                   p.service_state, p.service_window, p.top_drivers_json, p.major_uncertainties_json,
                   e.digest_json
            FROM published_forecast_state p
            LEFT JOIN engine_digest e ON e.prediction_run_id = p.source_prediction_run_id
            WHERE p.operator_id = ? AND p.service_date = ? AND p.service_window = 'dinner'
            ORDER BY p.last_published_at DESC
            LIMIT 1
            """,
            [operator_id, service_date],
        )
        if row is None:
            return ToolResult(tool_name="explain_forecast", success=True, data={},
                              message=f"No published forecast for {service_date}.")
        import json as _json
        top_drivers = _json.loads(row[7]) if row[7] else []
        major_uncertainties = _json.loads(row[8]) if row[8] else []
        digest = _json.loads(row[9]) if row[9] else {}
        service_window = str(row[6])
        posture = str(row[4])
        service_state = str(row[5])
        return ToolResult(
            tool_name="explain_forecast",
            success=True,
            data={
                "service_date": service_date,
                "forecast_expected": row[0],
                "range": f"{row[1]}-{row[2]}",
                "confidence_tier": row[3],
                "posture": posture,
                "service_state": service_state,
                "headline": forecast_headline(
                    service_window=service_window,
                    posture=posture,
                    service_state=service_state,
                ),
                "top_drivers": top_drivers,
                "major_uncertainties": major_uncertainties,
                "baseline": digest.get("baseline"),
                "vs_usual_pct": (
                    int(round(float(digest.get("total_pct")) * 100))
                    if digest.get("total_pct") not in {None, ""}
                    else None
                ),
                "vs_usual_covers": (
                    int(row[0]) - int(float(digest.get("baseline")))
                    if digest.get("baseline") not in {None, ""} and row[0] is not None
                    else None
                ),
                "weather_pct": digest.get("weather_pct"),
                "context_pct": digest.get("context_pct"),
                "seasonal_pct": digest.get("seasonal_pct"),
                "reference_status": digest.get("reference_status"),
                "reference_model": digest.get("reference_model"),
            },
        )

    def _exec_query_forecast_detail(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        result = self._exec_explain_forecast(operator_id, args)
        return ToolResult(
            tool_name="query_forecast_detail",
            success=result.success,
            data=result.data,
            message=result.message,
        )

    def _exec_query_hypothesis_backlog(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="query_hypothesis_backlog", success=False, message="I do not have a restaurant profile set up yet.")
        status = str(args.get("status") or "").strip().lower()
        if status and status not in {"open", "confirmed", "rejected", "stale"}:
            return ToolResult(
                tool_name="query_hypothesis_backlog",
                success=False,
                message="I can filter hypotheses by open, confirmed, rejected, or stale.",
            )
        limit = _bounded_int(args.get("limit"), default=12, minimum=1, maximum=30)
        if status:
            rows = self.db.fetchall(
                """
                SELECT hypothesis_key, status, confidence, hypothesis_value_json, evidence_json,
                       trigger_count, last_triggered_at, resolved_at, resolution_note, last_updated_at
                FROM operator_hypothesis_state
                WHERE operator_id = ?
                  AND status = ?
                ORDER BY last_updated_at DESC, last_triggered_at DESC
                LIMIT ?
                """,
                [operator_id, status, limit],
            )
        else:
            rows = self.db.fetchall(
                """
                SELECT hypothesis_key, status, confidence, hypothesis_value_json, evidence_json,
                       trigger_count, last_triggered_at, resolved_at, resolution_note, last_updated_at
                FROM operator_hypothesis_state
                WHERE operator_id = ?
                ORDER BY
                    CASE status WHEN 'open' THEN 0 WHEN 'confirmed' THEN 1 WHEN 'rejected' THEN 2 ELSE 3 END,
                    last_updated_at DESC,
                    last_triggered_at DESC
                LIMIT ?
                """,
                [operator_id, limit],
            )
        hypotheses = [
            {
                "hypothesis_key": str(row[0]),
                "status": str(row[1] or "open"),
                "confidence": str(row[2] or "low"),
                "hypothesis_value": _json_loads(row[3], None),
                "evidence": _json_loads(row[4], {}),
                "trigger_count": int(row[5] or 0),
                "last_triggered_at": row[6],
                "resolved_at": row[7],
                "resolution_note": row[8],
                "last_updated_at": row[9],
            }
            for row in rows
        ]
        return ToolResult(
            tool_name="query_hypothesis_backlog",
            success=True,
            data={"status": status or None, "hypotheses": hypotheses},
            message=f"Found {len(hypotheses)} hypothesis item(s).",
        )

    def _exec_query_learning_state(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        summary = self._exec_get_learning_summary(operator_id, {})
        if not summary.success:
            return ToolResult(
                tool_name="query_learning_state",
                success=False,
                data=summary.data,
                message=summary.message,
            )
        cascade = str(args.get("cascade") or "").strip().lower()
        if not cascade:
            return ToolResult(
                tool_name="query_learning_state",
                success=True,
                data=summary.data,
                message=summary.message,
            )
        key_by_alias = {
            "baseline": "baseline_learning",
            "confidence": "confidence_calibration",
            "calibration": "confidence_calibration",
            "weather": "weather_signatures",
            "weather_signature": "weather_signatures",
            "adaptation": "prediction_adaptations",
            "prediction_adaptation": "prediction_adaptations",
            "facts": "operator_facts",
            "operator_facts": "operator_facts",
            "observations": "recent_observations",
            "hypotheses": "open_hypotheses",
            "agenda": "learning_agenda",
            "decisions": "recent_learning_decisions",
            "equation": "equation_learning_state",
        }
        key = key_by_alias.get(cascade)
        if key is None:
            return ToolResult(
                tool_name="query_learning_state",
                success=False,
                data={"available_cascades": sorted(key_by_alias)},
                message="I do not recognize that learning-state area.",
            )
        return ToolResult(
            tool_name="query_learning_state",
            success=True,
            data={"cascade": cascade, key: summary.data.get(key, [])},
            message=f"I pulled the {cascade} learning state.",
        )

    def _exec_query_actuals_history(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="query_actuals_history", success=False, message="I do not have a restaurant profile set up yet.")
        limit = _bounded_int(args.get("limit"), default=10, minimum=1, maximum=50)
        state_filter = str(args.get("state_filter") or "").strip()
        params: list[Any] = [operator_id, operator_id]
        state_clause = ""
        if state_filter:
            state_clause = "AND a.service_state = ?"
            params.append(state_filter)
        params.append(limit)
        rows = self.db.fetchall(
            f"""
            WITH latest_eval AS (
                SELECT service_date, service_window, forecast_expected, forecast_low, forecast_high,
                       error_abs, error_pct, inside_interval,
                       ROW_NUMBER() OVER (
                           PARTITION BY service_date, service_window
                           ORDER BY evaluated_at DESC, evaluation_id DESC
                       ) AS rn
                FROM prediction_evaluations
                WHERE operator_id = ?
            )
            SELECT a.service_date, a.service_window, a.realized_total_covers,
                   a.realized_reserved_covers, a.realized_walk_in_covers, a.outside_covers,
                   a.service_state, a.entry_mode, a.note, a.entered_at, a.corrected_at,
                   e.forecast_expected, e.forecast_low, e.forecast_high,
                   e.error_abs, e.error_pct, e.inside_interval
            FROM operator_actuals a
            LEFT JOIN latest_eval e
              ON e.service_date = a.service_date
             AND e.service_window = a.service_window
             AND e.rn = 1
            WHERE a.operator_id = ?
              {state_clause}
            ORDER BY COALESCE(a.corrected_at, a.entered_at) DESC, a.service_date DESC
            LIMIT ?
            """,
            params,
        )
        actuals = [
            {
                "service_date": row[0],
                "service_window": row[1],
                "realized_total_covers": row[2],
                "realized_reserved_covers": row[3],
                "realized_walk_in_covers": row[4],
                "outside_covers": row[5],
                "service_state": row[6],
                "entry_mode": row[7],
                "note": row[8],
                "entered_at": row[9],
                "corrected_at": row[10],
                "forecast_expected": row[11],
                "forecast_low": row[12],
                "forecast_high": row[13],
                "error_abs": row[14],
                "error_pct": row[15],
                "inside_interval": row[16],
            }
            for row in rows
        ]
        return ToolResult(
            tool_name="query_actuals_history",
            success=True,
            data={"actuals": actuals, "state_filter": state_filter or None},
            message=f"Found {len(actuals)} actual row(s).",
        )

    def _exec_query_recent_signals(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="query_recent_signals", success=False, message="I do not have a restaurant profile set up yet.")
        limit = _bounded_int(args.get("limit"), default=10, minimum=1, maximum=50)
        dependency_group = str(args.get("dependency_group") or "").strip()
        if dependency_group:
            rows = self.db.fetchall(
                """
                SELECT signal_id, signal_type, source_name, source_class, source_bucket,
                       dependency_group, start_time, end_time, direction, strength,
                       recommended_role, details_json, status, origin_agent, created_at
                FROM external_signal_log
                WHERE operator_id = ?
                  AND dependency_group = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [operator_id, dependency_group, limit],
            )
        else:
            rows = self.db.fetchall(
                """
                SELECT signal_id, signal_type, source_name, source_class, source_bucket,
                       dependency_group, start_time, end_time, direction, strength,
                       recommended_role, details_json, status, origin_agent, created_at
                FROM external_signal_log
                WHERE operator_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [operator_id, limit],
            )
        signals = [
            {
                "signal_id": row[0],
                "signal_type": row[1],
                "source_name": row[2],
                "source_class": row[3],
                "source_bucket": row[4],
                "dependency_group": row[5],
                "start_time": row[6],
                "end_time": row[7],
                "direction": row[8],
                "strength": row[9],
                "recommended_role": row[10],
                "details": _json_loads(row[11], {}),
                "status": row[12],
                "origin_agent": row[13],
                "created_at": row[14],
            }
            for row in rows
        ]
        return ToolResult(
            tool_name="query_recent_signals",
            success=True,
            data={"signals": signals, "dependency_group": dependency_group or None},
            message=f"Found {len(signals)} recent signal(s).",
        )

    def _exec_capture_note(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="capture_note", success=False, message="I do not have a restaurant profile set up yet.")
        from stormready_v3.conversation.notes import ConversationNoteService
        note_text = args.get("note", "")
        service_date_str = args.get("service_date")
        service_date = date.fromisoformat(service_date_str) if service_date_str else None
        agent_service_state = args.get("service_state")  # extracted by conversation agent
        svc = ConversationNoteService(self.db, agent_dispatcher=self.agent_dispatcher)
        result = svc.record_note(
            operator_id=operator_id,
            note=note_text,
            service_date=service_date,
            service_window=ServiceWindow.DINNER,
            service_state_override=agent_service_state,
        )
        return ToolResult(
            tool_name="capture_note",
            success=True,
            data={
                "suggested_service_state": result.capture.suggested_service_state,
                "correction_staged": result.correction_suggestion_id is not None,
            },
            message="I recorded that note.",
        )

    def _exec_get_learning_summary(self, operator_id: str | None, _args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="get_learning_summary", success=False, message="I do not have a restaurant profile set up yet.")
        memory = ConversationMemoryService(self.db)
        reference_date = self._effective_reference_date()
        baseline_rows = self.db.fetchall(
            "SELECT day_group, baseline_mid, history_depth FROM baseline_learning_state WHERE operator_id = ?",
            [operator_id],
        )
        calibration_rows = self.db.fetchall(
            """SELECT horizon_mode, mean_abs_pct_error, interval_coverage_rate, sample_size, width_multiplier
            FROM confidence_calibration_state WHERE operator_id = ?""",
            [operator_id],
        )
        signature_rows = self.db.fetchall(
            "SELECT weather_signature, sensitivity_mid, sample_size FROM weather_signature_state WHERE operator_id = ? ORDER BY sample_size DESC LIMIT 5",
            [operator_id],
        )
        adaptation_rows = self.db.fetchall(
            """
            SELECT service_window, horizon_mode, adaptation_key, adjustment_mid, confidence, sample_size
            FROM prediction_adaptation_state
            WHERE operator_id = ?
            ORDER BY sample_size DESC, adaptation_key
            """,
            [operator_id],
        )
        digest_rows = self.db.fetchall(
            """
            WITH latest AS (
                SELECT service_date, service_window, digest_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY service_date, service_window
                           ORDER BY created_at DESC
                       ) AS rn
                FROM engine_digest
                WHERE operator_id = ?
                  AND service_date BETWEEN ? AND ?
            )
            SELECT service_date, service_window, digest_json
            FROM latest
            WHERE rn = 1
            ORDER BY service_date, service_window
            """,
            [operator_id, reference_date, reference_date.fromordinal(reference_date.toordinal() + 13)],
        )
        engine_digests: list[dict[str, Any]] = []
        for row in digest_rows:
            try:
                digest = json.loads(row[2])
            except (json.JSONDecodeError, TypeError):
                continue
            digest["service_date"] = row[0]
            digest["service_window"] = row[1]
            engine_digests.append(digest)
        return ToolResult(
            tool_name="get_learning_summary",
            success=True,
            data={
                "baseline_learning": [
                    {"day_group": r[0], "learned_baseline": r[1], "observations": r[2]}
                    for r in baseline_rows
                ],
                "confidence_calibration": [
                    {"horizon": r[0], "avg_error_pct": r[1], "coverage": r[2], "samples": r[3], "width_multiplier": r[4]}
                    for r in calibration_rows
                ],
                "weather_signatures": [
                    {"pattern": r[0], "sensitivity": r[1], "samples": r[2]}
                    for r in signature_rows
                ],
                "prediction_adaptations": [
                    {
                        "service_window": r[0],
                        "horizon_mode": r[1] or "",
                        "adaptation_key": r[2],
                        "adjustment_mid": r[3],
                        "confidence": r[4],
                        "samples": r[5],
                    }
                    for r in adaptation_rows
                ],
                "operator_facts": memory.load_active_facts(operator_id, limit=6),
                "recent_observations": memory.load_recent_observations(operator_id, limit=8),
                "open_hypotheses": memory.load_open_hypotheses(operator_id, limit=6),
                "learning_agenda": memory.load_learning_agenda(operator_id, limit=6),
                "recent_learning_decisions": memory.load_recent_learning_decisions(operator_id, limit=8),
                "prediction_equation": prediction_equation_contract(),
                "equation_learning_state": summarize_equation_learning_state(
                    open_hypotheses=memory.load_open_hypotheses(operator_id, limit=12),
                    learning_agenda=memory.load_learning_agenda(operator_id, limit=12),
                    recent_learning_decisions=memory.load_recent_learning_decisions(operator_id, limit=12),
                    engine_digests=engine_digests,
                ),
            },
            message="I pulled together the current learning signals.",
        )

    def _exec_request_refresh(self, operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        if operator_id is None:
            return ToolResult(tool_name="request_refresh", success=False, message="I do not have a restaurant profile set up yet.")
        from datetime import UTC, datetime
        from stormready_v3.domain.enums import RefreshReason
        from stormready_v3.runtime_bridge import maybe_run_supervisor_tick
        reason = args.get("reason", "operator requested")
        result = maybe_run_supervisor_tick(
            self.db,
            operator_id,
            now=datetime.now(UTC),
            force=True,
            refresh_reason=RefreshReason.OPERATOR_REQUESTED.value,
        )
        if result.get("error"):
            return ToolResult(
                tool_name="request_refresh",
                success=False,
                message=f"I could not refresh the forecast yet because {result['error']}.",
            )
        if result.get("ran_refresh"):
            return ToolResult(
                tool_name="request_refresh",
                success=True,
                data={"ran_refresh": True, "reason": reason},
                message="I refreshed the forecast. You are now looking at the latest update.",
            )
        if result.get("reason") == "refresh_in_progress":
            return ToolResult(
                tool_name="request_refresh",
                success=True,
                data={"ran_refresh": False, "reason": "refresh_in_progress"},
                message="A refresh is already running, so I will use that cycle instead of starting another one.",
            )
        return ToolResult(
            tool_name="request_refresh",
            success=True,
            data={"ran_refresh": False, "reason": "already_current"},
            message="The forecast is already current, so no refresh was needed.",
        )

    def _exec_interpret_upload(self, _operator_id: str | None, args: dict[str, Any]) -> ToolResult:
        # This tool returns the raw data for the agent to interpret via AI
        # The actual AI interpretation happens in the agent service, not here
        return ToolResult(
            tool_name="interpret_upload",
            success=True,
            data={
                "headers": args.get("headers", []),
                "sample_rows": args.get("sample_rows", []),
            },
            message="I have the file data ready to interpret.",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _json_loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _slugify(name: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "restaurant"


def _load_empty_location_context(operator_id: str, *, neighborhood_type: NeighborhoodType) -> LocationContextProfile:
    return LocationContextProfile(
        operator_id=operator_id,
        neighborhood_archetype=neighborhood_type,
    )


def _next_available_id(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    for i in range(2, 100):
        candidate = f"{base}_{i}"
        if candidate not in existing:
            return candidate
    return f"{base}_new"
