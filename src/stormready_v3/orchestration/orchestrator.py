from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
import json
from typing import Any

from stormready_v3.connectors.contracts import ConnectorTruthCandidate
from stormready_v3.connectors.factory import build_connector_registry
from stormready_v3.connectors.registry import ConnectorRegistry
from stormready_v3.domain.enums import (
    ForecastRegime,
    HorizonMode,
    PredictionCase,
    PublishDecision,
    RefreshReason,
    ServiceState,
    ServiceWindow,
)
from stormready_v3.domain.models import (
    CandidateForecastState,
    LocationContextProfile,
    NormalizedSignal,
    OperatorProfile,
    PredictionContext,
    PublishedForecastState,
)
from stormready_v3.agents.base import AgentContext, AgentDispatcher, AgentRole, AgentStatus
from stormready_v3.agents.contracts import PredictionGovernorOutput
from stormready_v3.agents.factory import build_agent_dispatcher
from stormready_v3.external_intelligence.catalog import ExternalSourceCatalogService
from stormready_v3.external_intelligence.signal_policy import summarize_dependency_group_corroboration
from stormready_v3.mvp_scope import ensure_runtime_window_supported, runtime_service_windows
from stormready_v3.operator.preferences import OperatorBehaviorService
from stormready_v3.operator_text import notification_payload_with_text
from stormready_v3.prediction.resolution import (
    resolve_service_state,
    resolve_sources,
    resolve_target,
    suggest_service_state_from_signals,
)
from stormready_v3.agents.runtime import PublishGovernor
from stormready_v3.ai.factory import build_agent_model_provider
from stormready_v3.orchestration.planner import RefreshPlan, plan_refresh_cycle
from stormready_v3.prediction.calendar import calendar_state_for_date, holiday_baseline_multiplier, holiday_service_risk
from stormready_v3.prediction.engine import day_group_for_date, default_regime_for_case, determine_regime, horizon_mode_for_day_offset, run_forecast
from stormready_v3.publish.policy import apply_notification_sensitivity, decide_publication, day_offset
from stormready_v3.reference.brooklyn import active_reference_override, use_reference_override
from stormready_v3.reference.operator_history import load_selected_operator_reference_override
from stormready_v3.sources.contracts import SourcePayload
from stormready_v3.sources.factory import build_source_registry
from stormready_v3.sources.normalization import (
    local_context_payload_to_signals,
    transit_payload_to_signals,
    weather_alert_payload_to_signals,
    weather_payload_to_reference_features,
    weather_payload_to_signals,
)
from stormready_v3.sources.registry import SourceRegistry
from stormready_v3.sources.weather_archive import compare_to_brooklyn, load_weather_baseline
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import AgentFrameworkRepository, ForecastRepository, OperatorRepository


_PREDICTION_GOVERNOR_DRIVER_PRIORITY = {
    "service_state_override": 0,
    "booked_reservation_anchor": 1,
    "brooklyn_weather_reference": 2,
    "nws_active_alert": 3,
    "weather_alert": 3,
    "weather_disruption_risk": 4,
    "precip_overlap": 5,
    "snow_risk": 6,
    "transit_disruption": 7,
    "baseline service window pattern": 99,
}


class DeterministicOrchestrator:
    def __init__(
        self,
        db: Database,
        publish_governor: PublishGovernor | None = None,
        source_registry: SourceRegistry | None = None,
        connector_registry: ConnectorRegistry | None = None,
        agent_dispatcher: AgentDispatcher | None = None,
    ) -> None:
        self.db = db
        provider = build_agent_model_provider()
        self._provider = provider
        self.operators = OperatorRepository(db)
        self.forecasts = ForecastRepository(db)
        self.agent_framework = AgentFrameworkRepository(db)
        self._agent_dispatcher = agent_dispatcher
        self._prediction_governor_dispatcher: AgentDispatcher | None = None
        self.operator_behavior = OperatorBehaviorService(db)
        self.external_catalog = ExternalSourceCatalogService(db, provider=provider)
        self.publish_governor = publish_governor or PublishGovernor()
        self.source_registry = source_registry or self._default_source_registry()
        self.connector_registry = connector_registry or self._default_connector_registry()

    def initialize(self) -> None:
        self.db.initialize()

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _default_source_registry() -> SourceRegistry:
        return build_source_registry()

    @staticmethod
    def _default_connector_registry() -> ConnectorRegistry:
        return build_connector_registry()

    @staticmethod
    def _apply_driver_emphasis(drivers: list[str], emphasized_indices: list[int]) -> list[str]:
        if not drivers or not emphasized_indices:
            return drivers
        ordered: list[str] = []
        seen: set[int] = set()
        for index in emphasized_indices:
            if 0 <= index < len(drivers) and index not in seen:
                ordered.append(drivers[index])
                seen.add(index)
        for index, driver in enumerate(drivers):
            if index in seen:
                continue
            ordered.append(driver)
        return ordered

    @staticmethod
    def _db_timestamp(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def _refresh_run_id(
        self,
        *,
        operator_id: str,
        refresh_reason: RefreshReason,
        run_date: date,
        executed_at: datetime,
    ) -> str:
        timestamp_token = executed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%f")
        return f"refresh_{operator_id}_{refresh_reason.value}_{run_date.isoformat()}_{timestamp_token}"

    def _start_refresh_run(
        self,
        *,
        operator_id: str,
        refresh_reason: RefreshReason,
        run_date: date,
        refresh_window: str | None,
        event_mode_active: bool,
        executed_at: datetime,
        source_summary: dict[str, object] | None = None,
    ) -> str:
        refresh_run_id = self._refresh_run_id(
            operator_id=operator_id,
            refresh_reason=refresh_reason,
            run_date=run_date,
            executed_at=executed_at,
        )
        self.db.execute(
            """
            INSERT INTO forecast_refresh_runs (
                refresh_run_id, operator_id, refresh_reason, run_date, refresh_window, started_at, completed_at, event_mode_active, source_summary_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                refresh_run_id,
                operator_id,
                refresh_reason.value,
                run_date,
                refresh_window,
                self._db_timestamp(executed_at),
                None,
                event_mode_active,
                json.dumps(source_summary or {}, default=str),
                "running",
            ],
        )
        return refresh_run_id

    def _complete_refresh_run(
        self,
        *,
        refresh_run_id: str,
        completed_at: datetime,
        status: str,
        source_summary: dict[str, object] | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE forecast_refresh_runs
            SET completed_at = ?, status = ?, source_summary_json = ?
            WHERE refresh_run_id = ?
            """,
            [
                self._db_timestamp(completed_at),
                status,
                json.dumps(source_summary or {}, default=str),
                refresh_run_id,
            ],
        )

    def determine_prediction_case(
        self,
        profile: OperatorProfile,
        baseline_total_covers: int | None,
        connector_truths: list[ConnectorTruthCandidate] | None = None,
    ) -> PredictionCase:
        connector_truths = connector_truths or []
        connector_types = {truth.system_type for truth in connector_truths}
        if "pos_connector" in connector_types and "reservation_connector" in connector_types:
            return PredictionCase.POS_AND_RESERVATION
        if "pos_connector" in connector_types:
            return PredictionCase.POS_ONLY
        if "reservation_connector" in connector_types:
            return PredictionCase.RESERVATION_ONLY
        if baseline_total_covers is not None:
            return PredictionCase.BASIC_PROFILE
        return PredictionCase.AMBIGUOUS

    def fetch_source_payloads(
        self,
        *,
        profile: OperatorProfile,
        service_date: date,
        service_window: ServiceWindow,
        fetched_at: datetime | None = None,
    ) -> tuple[list[SourcePayload], list[dict[str, str]]]:
        ensure_runtime_window_supported(service_window, context="source fetch")
        fetched_at = fetched_at or self.utc_now()
        payloads: list[SourcePayload] = []
        failures: list[dict[str, str]] = []
        for source_name in self.source_registry.list_sources():
            try:
                payloads.append(
                    self.source_registry.fetch(
                        source_name,
                        operator_id=profile.operator_id,
                        at=fetched_at,
                        profile=profile,
                        service_date=service_date,
                        service_window=service_window,
                    )
                )
            except Exception as exc:
                failures.append(
                    {
                        "source_name": source_name,
                        "error_class": exc.__class__.__name__,
                        "error_message": str(exc),
                    }
                )
        return payloads, failures

    def normalize_source_payloads(
        self,
        payloads: list[SourcePayload],
    ) -> list[NormalizedSignal]:
        signals: list[NormalizedSignal] = []
        for payload in payloads:
            if payload.source_class == "weather_forecast":
                signals.extend(weather_payload_to_signals(payload))
            elif payload.source_class == "weather_alert":
                signals.extend(weather_alert_payload_to_signals(payload))
            elif payload.source_class == "transit_disruption":
                signals.extend(transit_payload_to_signals(payload))
            elif payload.source_class in {"local_context", "agent_signal"}:
                signals.extend(local_context_payload_to_signals(payload))
        return signals

    def _build_operator_context_dict(self, operator_id: str) -> dict[str, str]:
        profile = self.operators.load_operator_profile(operator_id)
        location_context = self.operators.load_location_context(operator_id)
        if profile is None:
            return {"operator_id": operator_id}
        patio_exposure = "none"
        if profile.patio_enabled:
            capacity = profile.patio_seat_capacity
            patio_exposure = f"enabled:{capacity}" if capacity is not None else "enabled"
        return {
            "operator_id": operator_id,
            "restaurant_name": profile.restaurant_name,
            "neighborhood_type": profile.neighborhood_type.value,
            "venue_type": "restaurant",
            "demand_mix": profile.demand_mix.value,
            "patio_exposure": patio_exposure,
            "weather_sensitivity_hint": str(
                location_context.weather_sensitivity_hint
                if location_context is not None and location_context.weather_sensitivity_hint is not None
                else ""
            ),
        }

    @staticmethod
    def _serialize_payload_for_agent(payload: SourcePayload) -> dict[str, Any]:
        return {
            "source_name": payload.source_name,
            "source_class": payload.source_class,
            "source_bucket": payload.source_bucket,
            "payload": payload.payload,
        }

    def _fold_agent_signals_into_payloads(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        payloads: list[SourcePayload],
        prediction_run_id: str | None,
    ) -> list[SourcePayload]:
        import uuid

        ctx = AgentContext(
            role=AgentRole.SIGNAL_INTERPRETER,
            operator_id=operator_id,
            run_id=str(uuid.uuid4()),
            triggered_at=datetime.now(UTC),
            payload={
                "operator_context": self._build_operator_context_dict(operator_id),
                "service_date": service_date,
                "service_window": service_window.value,
                "payloads": [self._serialize_payload_for_agent(p) for p in payloads],
            },
        )
        result = self._agent_dispatcher.dispatch(ctx) if self._agent_dispatcher is not None else None
        if result is None or result.status is not AgentStatus.OK or not result.outputs:
            return payloads

        tier1_signals: list[dict[str, Any]] = []
        for signal in result.outputs:
            if signal.get("status") == "observed":
                tier1_signals.append(signal)
            elif signal.get("status") == "proposed":
                self.agent_framework.insert_agent_derived_external_signal(
                    operator_id=operator_id,
                    signal_id=str(uuid.uuid4()),
                    signal=signal,
                    status="proposed",
                    origin_agent=AgentRole.SIGNAL_INTERPRETER.value,
                    source_prediction_run_id=prediction_run_id,
                )
        if not tier1_signals:
            return payloads

        synthetic = SourcePayload(
            source_name=AgentRole.SIGNAL_INTERPRETER.value,
            source_class="agent_signal",
            retrieved_at=datetime.now(UTC),
            payload={
                "signals": [
                    {
                        "signal_type": str(signal["category"]),
                        "role": str(signal["role"]),
                        "details": {
                            "rationale": signal.get("rationale"),
                            "agent_category": signal.get("category"),
                            "agent_run_id": result.run_id,
                        },
                        "estimated_pct": (
                            float(signal["strength"])
                            if signal["direction"] == "up"
                            else -float(signal["strength"])
                            if signal["direction"] == "down"
                            else 0.0
                        ),
                        "dependency_group": str(signal["dependency_group"]),
                        "trust_level": "medium",
                        "direction": str(signal["direction"]),
                    }
                    for signal in tier1_signals
                ]
            },
            freshness="fresh",
            service_date=service_date,
            service_window=service_window.value,
            source_bucket="broad_proxy",
            scan_scope="agent_signal_scan",
            provenance={"origin": AgentRole.SIGNAL_INTERPRETER.value, "agent_run_id": result.run_id},
        )
        return [*payloads, synthetic]

    def fetch_connector_truths(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        fetched_at: datetime | None = None,
    ) -> tuple[list[ConnectorTruthCandidate], list[dict[str, str]]]:
        ensure_runtime_window_supported(service_window, context="connector truth fetch")
        fetched_at = fetched_at or self.utc_now()
        truths: list[ConnectorTruthCandidate] = []
        failures: list[dict[str, str]] = []
        for system_name, _system_type, _truth_priority_rank in self.operators.list_active_system_connections(operator_id):
            if system_name not in self.connector_registry.adapters:
                continue
            try:
                truth = self.connector_registry.fetch_truth(
                    system_name,
                    operator_id=operator_id,
                    at=fetched_at,
                    service_date=service_date,
                    service_window=service_window,
                )
            except Exception as exc:
                failures.append(
                    {
                        "system_name": system_name,
                        "error_class": exc.__class__.__name__,
                        "error_message": str(exc),
                    }
                )
                continue
            if truth is not None:
                truths.append(truth)
        return truths, failures

    def build_source_summary(
        self,
        *,
        payloads: list[SourcePayload],
        normalized_signals: list[NormalizedSignal],
        reference_feature_vector: dict[str, float] | None = None,
        connector_truths: list[ConnectorTruthCandidate] | None = None,
        source_failures: list[dict[str, str]] | None = None,
        connector_failures: list[dict[str, str]] | None = None,
        catalog_summary: dict[str, object] | None = None,
    ) -> dict[str, object]:
        connector_truths = connector_truths or []
        source_failures = source_failures or []
        connector_failures = connector_failures or []
        resolved_sources = resolve_sources(connector_truths=connector_truths)
        corroboration = summarize_dependency_group_corroboration(normalized_signals)
        signal_categories = [
            str(signal.details.get("source_category"))
            for signal in normalized_signals
            if signal.details.get("source_category") is not None
        ]
        return {
            "refresh_stage": "local_scaffold",
            "source_names": [payload.source_name for payload in payloads],
            "source_classes": [payload.source_class for payload in payloads],
            "source_buckets": [payload.source_bucket for payload in payloads],
            "source_freshness": {payload.source_name: payload.freshness for payload in payloads},
            "scan_scopes": [payload.scan_scope for payload in payloads if payload.scan_scope is not None],
            "signal_types": [signal.signal_type for signal in normalized_signals],
            "signal_buckets": [signal.source_bucket for signal in normalized_signals],
            "signal_categories": list(dict.fromkeys(signal_categories)),
            "connector_systems": [truth.system_name for truth in connector_truths],
            "source_failures": source_failures,
            "connector_failures": connector_failures,
            "resolved_sources": resolved_sources.resolved_source_summary,
            "source_conflicts": resolved_sources.conflicts,
            "brooklyn_reference_ready": reference_feature_vector is not None,
            "signal_corroboration": {
                dependency_group: {
                    "source_count": len(item.source_names),
                    "source_buckets": item.source_buckets,
                    "source_categories": item.source_categories,
                    "corroborated": item.corroborated,
                    "broad_numeric_eligible": item.broad_numeric_eligible,
                }
                for dependency_group, item in corroboration.items()
            },
            "uncorroborated_broad_proxy_groups": [
                dependency_group
                for dependency_group, item in corroboration.items()
                if "broad_proxy" in item.source_buckets and not item.broad_numeric_eligible
            ],
            "external_catalog": catalog_summary or {},
        }

    def persist_connector_evidence(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        prediction_run_id: str,
        connector_truths: list[ConnectorTruthCandidate],
    ) -> None:
        for index, truth in enumerate(connector_truths, start=1):
            self.db.execute(
                """
                INSERT INTO connector_truth_log (
                    connector_truth_id, operator_id, system_name, system_type, retrieved_at,
                    service_date, service_window, canonical_fields_json, field_quality_json,
                    provenance_json, source_prediction_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        f"{prediction_run_id}_connector_{index}",
                        operator_id,
                        truth.system_name,
                        truth.system_type,
                        self._db_timestamp(truth.extracted_at),
                        service_date,
                        service_window.value,
                        json.dumps(truth.fields, default=str),
                        json.dumps(truth.field_quality, default=str),
                    json.dumps(truth.provenance, default=str),
                    prediction_run_id,
                ],
            )

    def persist_source_evidence(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        prediction_run_id: str,
        payloads: list[SourcePayload],
        normalized_signals: list[NormalizedSignal],
    ) -> None:
        for payload in payloads:
            if payload.source_class == "weather_forecast":
                self.db.execute(
                    """
                    INSERT INTO weather_pulls (
                        operator_id, source_name, retrieved_at, forecast_for_date, service_window,
                        weather_feature_blob, raw_payload_ref, source_freshness
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        operator_id,
                        payload.source_name,
                        self._db_timestamp(payload.retrieved_at),
                        service_date,
                        service_window.value,
                        json.dumps(payload.payload),
                        json.dumps(
                            {
                                "prediction_run_id": prediction_run_id,
                                "provenance": payload.provenance,
                            }
                        ),
                        payload.freshness,
                    ],
                )

        for index, signal in enumerate(normalized_signals, start=1):
            self.db.execute(
                """
                INSERT INTO external_signal_log (
                    signal_id, operator_id, signal_type, source_name, source_class, dependency_group,
                    source_bucket, scan_scope, start_time, end_time, service_window_overlap, trust_level, direction, strength,
                    recommended_role, details_json, source_prediction_run_id, status, origin_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"{prediction_run_id}_signal_{index}",
                    operator_id,
                    signal.signal_type,
                    signal.source_name,
                    signal.source_class,
                    signal.dependency_group,
                    signal.source_bucket,
                    signal.scan_scope,
                    None,
                    None,
                    signal.service_window_overlap,
                    signal.trust_level,
                    signal.direction,
                    abs(signal.estimated_pct),
                    signal.role.value,
                    json.dumps(
                        {
                            "estimated_pct": signal.estimated_pct,
                            "details": signal.details,
                            "prediction_run_id": prediction_run_id,
                        }
                    ),
                    prediction_run_id,
                    "observed",
                    AgentRole.SIGNAL_INTERPRETER.value if signal.source_class == "agent_signal" else None,
                ],
            )

    def persist_service_state_resolution(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        resolved_service_state,
    ) -> None:
        if resolved_service_state.state_source == "default" and resolved_service_state.service_state is ServiceState.NORMAL:
            return
        if resolved_service_state.state_source == "operator":
            return
        self.db.execute(
            """
            INSERT INTO service_state_log (
                operator_id, service_date, service_window, service_state, source_type,
                source_name, confidence, operator_confirmed, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_date,
                service_window.value,
                resolved_service_state.service_state.value,
                resolved_service_state.state_source,
                resolved_service_state.state_source,
                resolved_service_state.state_confidence,
                False,
                resolved_service_state.state_resolution_reason,
            ],
        )

    def enqueue_notification_event(
        self,
        *,
        published: PublishedForecastState,
    ) -> None:
        payload = notification_payload_with_text(
            {
            "service_date": str(published.service_date),
            "service_window": published.service_window.value,
            "forecast_expected": published.forecast_expected,
            "forecast_low": published.forecast_low,
            "forecast_high": published.forecast_high,
            "confidence_tier": published.confidence_tier,
            "posture": published.posture,
            "service_state": published.service_state.value,
            "top_drivers": published.top_drivers,
            "major_uncertainties": published.major_uncertainties,
            }
        )
        self.db.execute(
            """
            INSERT INTO notification_events (
                operator_id, service_date, service_window, notification_type, publish_reason,
                source_prediction_run_id, payload_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                published.operator_id,
                published.service_date,
                published.service_window.value,
                "forecast_change",
                published.publish_reason,
                published.source_prediction_run_id,
                json.dumps(payload, default=str),
                "pending",
            ],
        )

    def build_context(
        self,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        service_date: date,
        service_window: ServiceWindow,
        baseline_total_covers: int | None = None,
        reference_date: date | None = None,
        normalized_signals: list[NormalizedSignal] | None = None,
        source_summary: dict[str, object] | None = None,
        connector_truths: list[ConnectorTruthCandidate] | None = None,
        reference_feature_vector: dict[str, float] | None = None,
    ) -> PredictionContext:
        reference_date = reference_date or date.today()
        offset = day_offset(reference_date, service_date)
        prediction_case = self.determine_prediction_case(profile, baseline_total_covers, connector_truths=connector_truths)
        horizon_mode: HorizonMode = horizon_mode_for_day_offset(max(0, offset))
        normalized_signals = normalized_signals or []
        resolved_sources = resolve_sources(connector_truths=connector_truths or [])
        resolved_target = resolve_target(connector_truths=connector_truths or [])
        calibration_state = self.load_confidence_calibration(
            operator_id=profile.operator_id,
            service_window=service_window,
            horizon_mode=horizon_mode,
        )
        # Graduate regime based on learning evidence
        baseline_depth = self._baseline_history_depth(profile.operator_id)
        forecast_regime: ForecastRegime = determine_regime(prediction_case, calibration_state, baseline_depth)
        weather_signature_learning = self.load_weather_signature_learning(
            operator_id=profile.operator_id,
            service_window=service_window,
        )
        external_scan_learning = self.load_external_scan_learning(
            operator_id=profile.operator_id,
        )
        source_reliability = self._load_source_reliability(profile.operator_id)
        prediction_adaptation_learning = self.load_prediction_adaptation_learning(
            operator_id=profile.operator_id,
            service_window=service_window,
            horizon_mode=horizon_mode,
        )
        operator_service_plan = self._load_operator_service_plan(
            operator_id=profile.operator_id,
            service_date=service_date,
            service_window=service_window,
        )
        service_state_risk = self.load_service_state_risk(
            operator_id=profile.operator_id,
            service_window=service_window,
            service_date=service_date,
        )
        # Load operator-confirmed service state if any
        operator_confirmed_state = self._load_operator_confirmed_state(
            profile.operator_id, service_date, service_window,
        )
        operator_note_state, operator_note_reason = self._load_recent_operator_note_state(
            profile.operator_id,
            service_date,
            service_window,
        )
        resolved_service_state = resolve_service_state(
            operator_state=operator_confirmed_state,
            operator_note_state=operator_note_state,
            operator_note_reason=operator_note_reason,
            connected_state=resolved_sources.authoritative_fields.get("service_state"),
            calendar_state=calendar_state_for_date(service_date),
            disruption_suggestion=suggest_service_state_from_signals(normalized_signals),
        )
        # Adjust baseline for holiday effects
        if baseline_total_covers is not None:
            holiday_mult = holiday_baseline_multiplier(service_date)
            if holiday_mult != 1.0:
                baseline_total_covers = max(0, round(baseline_total_covers * holiday_mult))
        current_state = self.forecasts.current_published_state(profile.operator_id, service_date, service_window)

        # Load weather baseline and compute Brooklyn similarity
        weather_baseline_normals = None
        brooklyn_similarity = None
        try:
            runtime_override = active_reference_override()
            if runtime_override is not None and runtime_override.similarity_override is not None:
                weather_baseline_normals = load_weather_baseline(self.db, profile.operator_id, service_window.value)
                brooklyn_similarity = float(runtime_override.similarity_override)
            else:
                weather_baseline_normals = load_weather_baseline(self.db, profile.operator_id, service_window.value)
                if weather_baseline_normals is not None:
                    similarity = compare_to_brooklyn(weather_baseline_normals, self.db)
                    brooklyn_similarity = similarity.overall_similarity
        except Exception:
            pass  # Weather baseline is enrichment — don't block prediction

        return PredictionContext(
            operator_profile=profile,
            location_context=location_context,
            service_date=service_date,
            service_window=service_window,
            resolved_target=resolved_target,
            resolved_service_state=resolved_service_state,
            prediction_case=prediction_case,
            forecast_regime=forecast_regime,
            horizon_mode=horizon_mode,
            baseline_total_covers=baseline_total_covers,
            resolved_truth_fields=resolved_sources.authoritative_fields,
            confidence_calibration=calibration_state,
            normalized_signals=normalized_signals,
            current_published_state={"raw_row": current_state} if current_state else None,
            source_summary=self._enrich_source_summary(
                source_summary or {"refresh_stage": "local_scaffold", "resolved_sources": resolved_sources.resolved_source_summary},
                profile.operator_id,
            ),
            reference_feature_vector=reference_feature_vector,
            weather_signature_learning=weather_signature_learning,
            external_scan_learning=external_scan_learning,
            source_reliability=source_reliability,
            prediction_adaptation_learning=prediction_adaptation_learning,
            service_state_risk=service_state_risk,
            operator_service_plan=operator_service_plan,
            weather_baseline_normals=weather_baseline_normals,
            brooklyn_similarity_score=brooklyn_similarity,
        )

    def _load_operator_service_plan(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
    ) -> dict[str, object]:
        row = self.db.fetchone(
            """
            SELECT planned_service_state, planned_total_covers, estimated_reduction_pct, raw_note,
                   review_window_start, review_window_end, updated_at
            FROM operator_service_plan
            WHERE operator_id = ?
              AND service_date = ?
              AND service_window = ?
            """,
            [operator_id, service_date, service_window.value],
        )
        if row is None:
            return {}
        return {
            "planned_service_state": str(row[0] or ServiceState.NORMAL.value),
            "planned_total_covers": int(row[1]) if row[1] is not None else None,
            "estimated_reduction_pct": float(row[2]) if row[2] is not None else None,
            "raw_note": str(row[3] or "").strip() or None,
            "review_window_start": row[4],
            "review_window_end": row[5],
            "updated_at": row[6],
        }

    def _load_operator_confirmed_state(
        self, operator_id: str, service_date: date, service_window: ServiceWindow,
    ) -> ServiceState | None:
        """Load the most recent operator-confirmed service state for this date/window."""
        row = self.db.fetchone(
            """
            SELECT service_state FROM service_state_log
            WHERE operator_id = ? AND service_date = ? AND service_window = ?
              AND operator_confirmed = TRUE
            ORDER BY created_at DESC, state_log_id DESC
            LIMIT 1
            """,
            [operator_id, service_date, service_window.value],
        )
        if row is None:
            return None
        try:
            return ServiceState(row[0])
        except ValueError:
            return None

    def _load_recent_operator_note_state(
        self,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
    ) -> tuple[ServiceState | None, str | None]:
        row = self.db.fetchone(
            """
            SELECT suggested_service_state, raw_note
            FROM conversation_note_log
            WHERE operator_id = ?
              AND service_date = ?
              AND service_window = ?
              AND suggested_service_state IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [operator_id, service_date, service_window.value],
        )
        if row is None:
            return None, None
        try:
            state = ServiceState(str(row[0]))
        except ValueError:
            return None, None
        raw_note = str(row[1] or "").strip()
        if raw_note:
            return state, f"recent operator note: {raw_note[:160]}"
        return state, "recent operator note"

    def _enrich_source_summary(self, base_summary: dict[str, Any], operator_id: str) -> dict[str, Any]:
        """Add component learning data that actively feeds prediction behavior."""
        summary = dict(base_summary)
        component_learning: dict[str, dict[str, Any]] = {}

        reservation_row = self.db.fetchone(
            """
            SELECT observation_count, component_state
            FROM component_learning_state
            WHERE operator_id = ? AND component_name = 'realized_reserved_covers'
            """,
            [operator_id],
        )
        if reservation_row and int(reservation_row[0] or 0) >= 3:
            share_row = self.db.fetchone(
                """
                SELECT AVG(reservation_share)
                FROM (
                    SELECT
                        CAST(realized_reserved_covers AS DOUBLE) / realized_total_covers AS reservation_share
                    FROM operator_actuals
                    WHERE operator_id = ?
                      AND realized_reserved_covers IS NOT NULL
                      AND realized_total_covers > 0
                    ORDER BY service_date DESC
                    LIMIT 20
                ) recent_actuals
                """,
                [operator_id],
            )
            component_learning["realized_reserved_covers"] = {
                "observation_count": int(reservation_row[0]),
                "component_state": reservation_row[1],
                "observed_reservation_share": float(share_row[0]) if share_row and share_row[0] else None,
            }

        outside_row = self.db.fetchone(
            """
            SELECT observation_count, component_state
            FROM component_learning_state
            WHERE operator_id = ? AND component_name = 'outside_covers'
            """,
            [operator_id],
        )
        if outside_row and int(outside_row[0] or 0) >= 3:
            outside_share_row = self.db.fetchone(
                """
                SELECT AVG(outside_share)
                FROM (
                    SELECT
                        CAST(outside_covers AS DOUBLE) / realized_total_covers AS outside_share
                    FROM operator_actuals
                    WHERE operator_id = ?
                      AND outside_covers IS NOT NULL
                      AND realized_total_covers > 0
                    ORDER BY service_date DESC
                    LIMIT 20
                ) recent_actuals
                """,
                [operator_id],
            )
            component_learning["outside_covers"] = {
                "observation_count": int(outside_row[0]),
                "component_state": outside_row[1],
                "observed_outside_share": float(outside_share_row[0]) if outside_share_row and outside_share_row[0] else None,
            }

        if component_learning:
            summary["component_learning"] = component_learning
        return summary

    def _load_source_reliability(self, operator_id: str) -> dict[str, dict[str, Any]]:
        """Load learned source reliability keyed by signal_type."""
        rows = self.db.fetchall(
            """
            SELECT signal_type, historical_usefulness_score, trust_class
            FROM source_reliability_state
            WHERE operator_id = ? AND status = 'active'
            """,
            [operator_id],
        )
        return {
            row[0]: {
                "usefulness_score": row[1],
                "trust_class": row[2],
            }
            for row in rows
        }

    def _baseline_history_depth(self, operator_id: str) -> int:
        """Return the max history_depth across all day groups for this operator."""
        row = self.db.fetchone(
            "SELECT MAX(history_depth) FROM baseline_learning_state WHERE operator_id = ?",
            [operator_id],
        )
        return int(row[0]) if row and row[0] else 0

    @staticmethod
    def _build_learning_context_for_governor(context: PredictionContext) -> dict[str, object]:
        """Compact learning summary for the PredictionGovernor AI prompt."""
        cal = context.confidence_calibration
        sample_size = int(cal.get("sample_size", 0) or 0)
        loc = context.location_context
        return {
            "forecast_regime": context.forecast_regime.value,
            "baseline_history_depth": sample_size,
            "calibration_sample_size": sample_size,
            "calibration_error_pct": float(cal.get("mean_abs_pct_error", 0) or 0),
            "calibration_coverage": float(cal.get("interval_coverage_rate", 0) or 0),
            "width_multiplier": float(cal.get("width_multiplier", 1.0) or 1.0),
            "weather_signatures_learned": len(context.weather_signature_learning),
            "source_reliability_entries": len(context.source_reliability),
            "transit_relevance": loc.transit_relevance,
            "venue_relevance": loc.venue_relevance,
            "hotel_travel_relevance": loc.hotel_travel_relevance,
            "brooklyn_similarity": context.brooklyn_similarity_score,
        }

    @staticmethod
    def _build_prediction_governor_heuristics(candidate: "CandidateForecastState") -> dict[str, object]:
        clarification_needed = False
        clarification_question = None
        uncertainty_notes = list(dict.fromkeys(candidate.major_uncertainties))

        if candidate.target_definition_confidence in {"low", "low_medium"}:
            clarification_needed = True
            clarification_question = (
                "If you have reservation, walk-in, or waitlist detail for this service, logging it will improve future forecasts."
            )
            uncertainty_notes.append("component truth is still developing")

        if (
            candidate.service_state is not ServiceState.NORMAL
            and candidate.service_state_reason
            and "suggestion" in candidate.service_state_reason.lower()
        ):
            clarification_needed = True
            clarification_question = "The service state looks abnormal. Confirming whether service was limited will improve forecast reliability."
            uncertainty_notes.append("service state may still need operator confirmation")

        if candidate.confidence_tier == "very_low":
            uncertainty_notes.append("treat this forecast as directional rather than precise")

        return {
            "clarification_needed": clarification_needed,
            "clarification_question": clarification_question,
            "uncertainty_notes": list(dict.fromkeys(uncertainty_notes))[:3],
        }

    @staticmethod
    def _serialize_prediction_governor_candidate(candidate: "CandidateForecastState") -> dict[str, object]:
        return {
            "service_date": candidate.service_date.isoformat(),
            "service_window": candidate.service_window.value,
            "forecast_expected": candidate.forecast_expected,
            "forecast_low": candidate.forecast_low,
            "forecast_high": candidate.forecast_high,
            "confidence_tier": candidate.confidence_tier,
            "service_state": candidate.service_state.value,
            "service_state_reason": candidate.service_state_reason,
            "prediction_case": candidate.prediction_case.value,
            "forecast_regime": candidate.forecast_regime.value,
            "horizon_mode": candidate.horizon_mode.value,
            "top_drivers": list(candidate.top_drivers),
            "major_uncertainties": list(candidate.major_uncertainties),
            "target_definition_confidence": candidate.target_definition_confidence,
            "realized_total_truth_quality": candidate.realized_total_truth_quality,
            "component_truth_quality": candidate.component_truth_quality,
            "reference_status": candidate.reference_status,
            "reference_model": candidate.reference_model,
        }

    def _resolve_prediction_governor_dispatcher(self) -> AgentDispatcher | None:
        if self._agent_dispatcher is not None:
            return self._agent_dispatcher
        if self._prediction_governor_dispatcher is not None:
            return self._prediction_governor_dispatcher
        try:
            self._prediction_governor_dispatcher = build_agent_dispatcher(
                self.db,
                self._provider,
            )
        except Exception:  # noqa: BLE001
            return None
        return self._prediction_governor_dispatcher

    @staticmethod
    def _deterministic_prediction_governor_output(
        candidate: "CandidateForecastState",
    ) -> PredictionGovernorOutput:
        heuristics = DeterministicOrchestrator._build_prediction_governor_heuristics(candidate)
        emphasized = sorted(
            range(len(candidate.top_drivers)),
            key=lambda index: (
                _PREDICTION_GOVERNOR_DRIVER_PRIORITY.get(candidate.top_drivers[index], 50),
                candidate.top_drivers[index],
            ),
        )
        emphasized = emphasized[: min(3, len(emphasized))]
        return PredictionGovernorOutput(
            emphasized_driver_indices=emphasized,
            clarification_needed=bool(heuristics["clarification_needed"]),
            clarification_question=(
                str(heuristics["clarification_question"]).strip()
                if heuristics["clarification_question"]
                else None
            ),
            uncertainty_notes=[
                str(item).strip()
                for item in list(heuristics["uncertainty_notes"])
                if str(item).strip()
            ][:3],
            governance_path="deterministic_base",
        )

    def _dispatch_prediction_governor(
        self,
        *,
        candidate: "CandidateForecastState",
        learning_context: dict[str, object],
    ) -> PredictionGovernorOutput | None:
        dispatcher = self._resolve_prediction_governor_dispatcher()
        if dispatcher is None:
            return None

        heuristics = self._build_prediction_governor_heuristics(candidate)
        run_id_seed = candidate.source_prediction_run_id or (
            f"{candidate.operator_id}::{candidate.service_date.isoformat()}::{candidate.service_window.value}"
        )
        run_id = f"prediction_governor::{run_id_seed}"
        ctx = AgentContext(
            role=AgentRole.PREDICTION_GOVERNOR,
            operator_id=candidate.operator_id,
            run_id=run_id,
            triggered_at=self.utc_now(),
            payload={
                "service_date": candidate.service_date.isoformat(),
                "service_window": candidate.service_window.value,
                "service_state": candidate.service_state.value,
                "phase": "operations",
                "candidate": self._serialize_prediction_governor_candidate(candidate),
                "heuristic_summary": heuristics,
                "learning_context": learning_context,
            },
        )
        result = dispatcher.dispatch(ctx)
        if result.status is not AgentStatus.OK or not result.outputs:
            return None

        output = result.outputs[0]
        emphasized_raw = output.get("emphasized_driver_indices")
        emphasized_indices = [
            int(index)
            for index in emphasized_raw
            if isinstance(index, int)
        ] if isinstance(emphasized_raw, list) else []
        if not emphasized_indices:
            emphasized_names = output.get("emphasized_drivers") or []
            if isinstance(emphasized_names, list):
                emphasized_lookup = {str(item) for item in emphasized_names}
                emphasized_indices = [
                    index for index, driver in enumerate(candidate.top_drivers) if driver in emphasized_lookup
                ]

        uncertainty_raw = output.get("uncertainty_notes") or []
        uncertainty_notes = [
            str(item).strip() for item in uncertainty_raw if str(item).strip()
        ] if isinstance(uncertainty_raw, list) else []

        return PredictionGovernorOutput(
            emphasized_driver_indices=emphasized_indices[:3],
            clarification_needed=bool(output.get("clarification_needed")),
            clarification_question=str(output.get("clarification_question") or "").strip() or None,
            uncertainty_notes=uncertainty_notes[:3],
            governance_path=str(output.get("governance_path") or "ai"),
        )

    def _govern_prediction_candidate(
        self,
        *,
        candidate: "CandidateForecastState",
        learning_context: dict[str, object],
    ) -> PredictionGovernorOutput:
        dispatched = self._dispatch_prediction_governor(
            candidate=candidate,
            learning_context=learning_context,
        )
        if dispatched is not None:
            return dispatched
        return self._deterministic_prediction_governor_output(candidate)

    def load_confidence_calibration(
        self,
        *,
        operator_id: str,
        service_window: ServiceWindow,
        horizon_mode: HorizonMode,
    ) -> dict[str, object]:
        row = self.db.fetchone(
            """
            SELECT mean_abs_pct_error, interval_coverage_rate, sample_size, width_multiplier, confidence_penalty_steps
            FROM confidence_calibration_state
            WHERE operator_id = ? AND service_window = ? AND horizon_mode = ?
            """,
            [operator_id, service_window.value, horizon_mode.value],
        )
        if row is None:
            return {}
        return {
            "mean_abs_pct_error": float(row[0] or 0.0),
            "interval_coverage_rate": float(row[1] or 0.0),
            "sample_size": int(row[2] or 0),
            "width_multiplier": float(row[3] or 1.0),
            "confidence_penalty_steps": int(row[4] or 0),
        }

    def load_weather_signature_learning(
        self,
        *,
        operator_id: str,
        service_window: ServiceWindow,
    ) -> dict[str, dict[str, object]]:
        rows = self.db.fetchall(
            """
            SELECT weather_signature, sensitivity_mid, confidence, sample_size
            FROM weather_signature_state
            WHERE operator_id = ? AND service_window = ?
            """,
            [operator_id, service_window.value],
        )
        return {
            str(row[0]): {
                "sensitivity_mid": float(row[1] or 0.0),
                "confidence": str(row[2] or "low"),
                "sample_size": int(row[3] or 0),
            }
            for row in rows
        }

    def load_external_scan_learning(
        self,
        *,
        operator_id: str,
    ) -> dict[str, dict[str, object]]:
        rows = self.db.fetchall(
            """
            SELECT source_bucket, scan_scope, dependency_group, estimated_effect, usefulness_score, confidence, sample_size
            FROM external_scan_learning_state
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        return {
            f"{str(row[0])}|{str(row[1]) if row[1] is not None else ''}|{str(row[2])}": {
                "estimated_effect": float(row[3] or 0.0),
                "usefulness_score": float(row[4] or 0.0),
                "confidence": str(row[5] or "low"),
                "sample_size": int(row[6] or 0),
            }
            for row in rows
        }

    def load_prediction_adaptation_learning(
        self,
        *,
        operator_id: str,
        service_window: ServiceWindow,
        horizon_mode: HorizonMode,
    ) -> dict[str, dict[str, object]]:
        rows = self.db.fetchall(
            """
            SELECT adaptation_key, adjustment_mid, confidence, sample_size, horizon_mode
            FROM prediction_adaptation_state
            WHERE operator_id = ?
              AND service_window = ?
              AND (horizon_mode = '' OR horizon_mode = ?)
            """,
            [operator_id, service_window.value, horizon_mode.value],
        )
        return {
            str(row[0]): {
                "adjustment_mid": float(row[1] or 0.0),
                "confidence": str(row[2] or "low"),
                "sample_size": int(row[3] or 0),
                "horizon_mode": str(row[4] or ""),
            }
            for row in rows
        }

    def load_service_state_risk(
        self,
        *,
        operator_id: str,
        service_window: ServiceWindow,
        service_date: date,
    ) -> dict[str, object]:
        day_group = day_group_for_date(service_date)
        rows = self.db.fetchall(
            """
            SELECT risk_state, risk_score, confidence, abnormal_observation_weight, normal_observation_weight
            FROM service_state_risk_state
            WHERE operator_id = ?
              AND service_window = ?
              AND day_group = ?
              AND risk_score >= 0.08
            ORDER BY risk_score DESC
            """,
            [operator_id, service_window.value, day_group],
        )
        learned = [
            {
                "risk_state": str(row[0]),
                "risk_score": float(row[1] or 0.0),
                "confidence": str(row[2] or "low"),
                "abnormal_observation_weight": float(row[3] or 0.0),
                "normal_observation_weight": float(row[4] or 0.0),
                "source": "learned_actuals",
            }
            for row in rows
        ]
        calendar_risk = holiday_service_risk(service_date)
        candidates = list(learned)
        if calendar_risk is not None:
            candidates.append(dict(calendar_risk))
        if not candidates:
            return {"day_group": day_group, "risk_score": 0.0, "risk_state": None, "sources": []}
        top = max(candidates, key=lambda item: float(item.get("risk_score") or 0.0))
        return {
            "day_group": day_group,
            "risk_state": top.get("risk_state"),
            "risk_score": float(top.get("risk_score") or 0.0),
            "confidence": str(top.get("confidence") or "low"),
            "reason": top.get("reason"),
            "source": top.get("source"),
            "sources": candidates,
        }

    def refresh_with_stored_baseline(
        self,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        service_date: date,
        service_window: ServiceWindow,
        refresh_reason: RefreshReason = RefreshReason.SCHEDULED,
        reference_date: date | None = None,
        refresh_window: str | None = None,
        executed_at: datetime | None = None,
        catalog_summary: dict[str, object] | None = None,
        refresh_run_id: str | None = None,
    ) -> PublishedForecastState | None:
        ensure_runtime_window_supported(service_window, context="forecast refresh")
        baseline = self.operators.weekly_baseline_for(
            profile.operator_id,
            service_window,
            day_group_for_date(service_date),
        )
        return self.refresh_forecast(
            profile=profile,
            location_context=location_context,
            service_date=service_date,
            service_window=service_window,
            baseline_total_covers=baseline,
            refresh_reason=refresh_reason,
            reference_date=reference_date,
            refresh_window=refresh_window,
            executed_at=executed_at,
            catalog_summary=catalog_summary,
            refresh_run_id=refresh_run_id,
        )

    def refresh_forecast(
        self,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        service_date: date,
        service_window: ServiceWindow,
        baseline_total_covers: int | None = None,
        refresh_reason: RefreshReason = RefreshReason.SCHEDULED,
        reference_date: date | None = None,
        refresh_window: str | None = None,
        executed_at: datetime | None = None,
        catalog_summary: dict[str, object] | None = None,
        refresh_run_id: str | None = None,
    ) -> PublishedForecastState | None:
        ensure_runtime_window_supported(service_window, context="forecast refresh")
        reference_date = reference_date or date.today()
        executed_at = executed_at or self.utc_now()
        db_executed_at = self._db_timestamp(executed_at)
        source_payloads, source_failures = self.fetch_source_payloads(
            profile=profile,
            service_date=service_date,
            service_window=service_window,
            fetched_at=executed_at,
        )
        if self._agent_dispatcher is not None:
            source_payloads = self._fold_agent_signals_into_payloads(
                operator_id=profile.operator_id,
                service_date=service_date,
                service_window=service_window,
                payloads=source_payloads,
                prediction_run_id=None,
            )
        connector_truths, connector_failures = self.fetch_connector_truths(
            operator_id=profile.operator_id,
            service_date=service_date,
            service_window=service_window,
            fetched_at=executed_at,
        )
        normalized_signals = self.normalize_source_payloads(source_payloads)
        reference_override = (
            load_selected_operator_reference_override(
                self.db,
                operator_id=profile.operator_id,
            )
            if profile.setup_mode == "historical_upload"
            else None
        )
        with use_reference_override(reference_override):
            reference_feature_vector = None
            for payload in source_payloads:
                if payload.source_class != "weather_forecast":
                    continue
                reference_feature_vector = weather_payload_to_reference_features(payload)
                if reference_feature_vector is not None:
                    break
            context = self.build_context(
                profile,
                location_context,
                service_date,
                service_window,
                baseline_total_covers,
                reference_date=reference_date,
                normalized_signals=normalized_signals,
                source_summary=self.build_source_summary(
                    payloads=source_payloads,
                    normalized_signals=normalized_signals,
                    reference_feature_vector=reference_feature_vector,
                    connector_truths=connector_truths,
                    source_failures=source_failures,
                    connector_failures=connector_failures,
                    catalog_summary=catalog_summary,
                ),
                connector_truths=connector_truths,
                reference_feature_vector=reference_feature_vector,
            )
            candidate, engine_digest = run_forecast(context)
        candidate.generated_at = executed_at
        self.persist_service_state_resolution(
            operator_id=profile.operator_id,
            service_date=service_date,
            service_window=service_window,
            resolved_service_state=context.resolved_service_state,
        )
        if "realized_total_covers" in context.resolved_truth_fields:
            candidate.realized_total_truth_quality = "connected"
        elif "booked_reservation_covers" in context.resolved_truth_fields:
            candidate.realized_total_truth_quality = "reservation_connected"
        if context.resolved_target.available_components:
            candidate.component_truth_quality = "partial_connected"
        elif "booked_reservation_covers" in context.resolved_truth_fields:
            candidate.component_truth_quality = "reservation_intent_connected"
        learning_context = self._build_learning_context_for_governor(context)
        prediction_governance = self._govern_prediction_candidate(
            candidate=candidate,
            learning_context=learning_context,
        )
        publish_governance = self.publish_governor.govern(candidate)
        candidate.top_drivers = self._apply_driver_emphasis(
            candidate.top_drivers,
            prediction_governance.emphasized_driver_indices,
        )
        current_state = self.forecasts.current_published_state(profile.operator_id, service_date, service_window)
        decision = decide_publication(candidate, reference_date, current_state, refresh_reason)
        behavior_preferences = self.operator_behavior.load_preferences(profile.operator_id)
        decision = apply_notification_sensitivity(
            decision,
            notification_sensitivity=(
                behavior_preferences.notification_sensitivity
                if behavior_preferences is not None
                else None
            ),
        )

        if publish_governance.override_notify is True:
            decision.should_notify = True
            if decision.publish_decision is PublishDecision.PUBLISH:
                decision.publish_decision = PublishDecision.PUBLISH_AND_NOTIFY
        if publish_governance.additional_publish_reason:
            decision.snapshot_reason = (
                f"{decision.snapshot_reason}+{publish_governance.additional_publish_reason}"
                if decision.snapshot_reason
                else publish_governance.additional_publish_reason
            )

        self.forecasts.insert_prediction_run(
            prediction_run_id=candidate.source_prediction_run_id,
            operator_id=candidate.operator_id,
            service_date=candidate.service_date,
            service_window=candidate.service_window,
            prediction_case=candidate.prediction_case.value,
            forecast_regime=candidate.forecast_regime.value,
            horizon_mode=candidate.horizon_mode.value,
            target_name=candidate.target_name,
            generator_version="stormready_v3_scaffold",
            context_packet_json=json.dumps({"context": asdict(context)}, default=str),
            generated_at=self._db_timestamp(candidate.generated_at),
            reference_status=candidate.reference_status,
            reference_model=candidate.reference_model,
            reference_details_json=json.dumps(
                {
                    "brooklyn_reference_ready": context.reference_feature_vector is not None,
                    "reference_feature_keys": sorted((context.reference_feature_vector or {}).keys()),
                }
            ),
        )
        self.forecasts.insert_prediction_components(
            [
                (
                    component_prediction.prediction_run_id,
                    component_prediction.component_name,
                    component_prediction.component_state.value,
                    component_prediction.predicted_value,
                    component_prediction.is_operator_visible,
                    component_prediction.is_learning_eligible,
                )
                for component_prediction in candidate.component_predictions
            ]
        )
        self.forecasts.upsert_engine_digest(
            prediction_run_id=candidate.source_prediction_run_id,
            operator_id=profile.operator_id,
            service_date=service_date,
            service_window=service_window,
            digest_json=json.dumps(engine_digest, default=str),
        )
        self.persist_source_evidence(
            operator_id=profile.operator_id,
            service_date=service_date,
            service_window=service_window,
            prediction_run_id=candidate.source_prediction_run_id,
            payloads=source_payloads,
            normalized_signals=normalized_signals,
        )
        self.persist_connector_evidence(
            operator_id=profile.operator_id,
            service_date=service_date,
            service_window=service_window,
            prediction_run_id=candidate.source_prediction_run_id,
            connector_truths=connector_truths,
        )

        if decision.destination.value == "working_forecast_state":
            self.forecasts.upsert_working_forecast_state(
                operator_id=candidate.operator_id,
                service_date=candidate.service_date,
                service_window=candidate.service_window,
                target_name=candidate.target_name,
                forecast_expected=candidate.forecast_expected,
                forecast_low=candidate.forecast_low,
                forecast_high=candidate.forecast_high,
                confidence_tier=candidate.confidence_tier,
                posture=candidate.posture,
                service_state=candidate.service_state.value,
                source_prediction_run_id=candidate.source_prediction_run_id,
                reference_status=candidate.reference_status,
                reference_model=candidate.reference_model,
                refresh_reason=refresh_reason.value,
                refreshed_at=db_executed_at,
            )
            return None

        next_version = 1
        if current_state is not None:
            next_version = int(current_state[3]) + 1

        published = PublishedForecastState(
            operator_id=candidate.operator_id,
            service_date=candidate.service_date,
            service_window=candidate.service_window,
            state_version=next_version,
            active_service_windows=profile.active_service_windows,
            target_name=candidate.target_name,
            forecast_expected=candidate.forecast_expected,
            forecast_low=candidate.forecast_low,
            forecast_high=candidate.forecast_high,
            confidence_tier=candidate.confidence_tier,
            posture=candidate.posture,
            service_state=candidate.service_state,
            service_state_reason=candidate.service_state_reason,
            prediction_case=candidate.prediction_case,
            forecast_regime=candidate.forecast_regime,
            horizon_mode=candidate.horizon_mode,
            top_drivers=candidate.top_drivers,
            major_uncertainties=candidate.major_uncertainties,
            target_definition_confidence=candidate.target_definition_confidence,
            realized_total_truth_quality=candidate.realized_total_truth_quality,
            component_truth_quality=candidate.component_truth_quality,
            resolved_source_summary=candidate.resolved_source_summary,
            source_prediction_run_id=candidate.source_prediction_run_id,
            reference_status=candidate.reference_status,
            reference_model=candidate.reference_model,
            publish_reason=decision.snapshot_reason or refresh_reason.value,
            publish_decision=decision.publish_decision,
            last_published_at=db_executed_at,
        )
        if prediction_governance.uncertainty_notes:
            published.major_uncertainties = list(dict.fromkeys(published.major_uncertainties + prediction_governance.uncertainty_notes))
        if prediction_governance.clarification_needed and prediction_governance.clarification_question:
            published.major_uncertainties = list(
                dict.fromkeys(published.major_uncertainties + [prediction_governance.clarification_question])
            )
        self.forecasts.store_published_state(published)

        if decision.snapshot_reason:
            self.forecasts.insert_forecast_publication_snapshot(
                operator_id=published.operator_id,
                service_date=published.service_date,
                service_window=published.service_window,
                state_version=published.state_version,
                target_name=published.target_name,
                forecast_expected=published.forecast_expected,
                forecast_low=published.forecast_low,
                forecast_high=published.forecast_high,
                confidence_tier=published.confidence_tier,
                posture=published.posture,
                service_state=published.service_state.value,
                source_prediction_run_id=published.source_prediction_run_id,
                reference_status=published.reference_status,
                reference_model=published.reference_model,
                snapshot_reason=decision.snapshot_reason,
                snapshot_at=db_executed_at,
            )
        if decision.should_notify:
            self.enqueue_notification_event(published=published)

        return published

    def run_refresh_cycle(
        self,
        *,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        refresh_reason: RefreshReason,
        run_date: date | None = None,
        refresh_window: str | None = None,
        event_mode_active: bool = False,
        service_windows: list[ServiceWindow] | None = None,
        executed_at: datetime | None = None,
    ) -> RefreshPlan:
        run_date = run_date or date.today()
        executed_at = executed_at or self.utc_now()
        plan = plan_refresh_cycle(
            run_date=run_date,
            reason=refresh_reason,
            refresh_window=refresh_window,
            event_mode_active=event_mode_active,
        )
        catalog_summary = self.external_catalog.run_refresh_discovery(
            profile=profile,
            location_context=location_context,
            run_date=run_date,
            refresh_reason=refresh_reason,
            refresh_window=refresh_window,
            scanned_at=executed_at,
        )
        target_windows = runtime_service_windows(service_windows or profile.active_service_windows or [profile.primary_service_window])
        refresh_summary: dict[str, object] = {
            "catalog_summary": catalog_summary,
            "service_windows": [window.value for window in target_windows],
            "actionable_dates": [item.isoformat() for item in plan.actionable_dates],
            "working_dates": [item.isoformat() for item in plan.working_dates],
        }
        refresh_run_id = self._start_refresh_run(
            operator_id=profile.operator_id,
            refresh_reason=refresh_reason,
            run_date=run_date,
            refresh_window=plan.refresh_window,
            event_mode_active=event_mode_active,
            executed_at=executed_at,
            source_summary=refresh_summary,
        )

        try:
            for service_window in target_windows:
                for service_date in plan.actionable_dates + plan.working_dates:
                    self.refresh_with_stored_baseline(
                        profile=profile,
                        location_context=location_context,
                        service_date=service_date,
                        service_window=service_window,
                        refresh_reason=refresh_reason,
                        reference_date=run_date,
                        refresh_window=plan.refresh_window,
                        executed_at=executed_at,
                        catalog_summary=catalog_summary,
                        refresh_run_id=refresh_run_id,
                    )
            self._complete_refresh_run(
                refresh_run_id=refresh_run_id,
                completed_at=self.utc_now(),
                status="completed",
                source_summary=refresh_summary,
            )
            if self._agent_dispatcher is not None:
                from stormready_v3.workflows.retriever_hooks import run_retriever_hooks

                run_retriever_hooks(
                    db=self.db,
                    dispatcher=self._agent_dispatcher,
                    operator_id=profile.operator_id,
                    reference_date=run_date,
                    kinds=("current_state",),
                )
            return plan
        except Exception as exc:
            refresh_summary["error"] = str(exc)
            self._complete_refresh_run(
                refresh_run_id=refresh_run_id,
                completed_at=self.utc_now(),
                status="failed",
                source_summary=refresh_summary,
            )
            raise
