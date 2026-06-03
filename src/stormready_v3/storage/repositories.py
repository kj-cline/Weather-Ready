from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from stormready_v3.domain.enums import DemandMix, NeighborhoodType, OnboardingState, ServiceWindow
from stormready_v3.domain.models import LocationContextProfile, OperatorProfile, PublishedForecastState
from stormready_v3.storage.db import Database


def _db_timestamp(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class OperatorRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_operator(self, profile: OperatorProfile) -> None:
        now = datetime.now(UTC)
        setup_mode = profile.setup_mode
        if setup_mode is None:
            setup_mode = (
                "baseline_first"
                if profile.onboarding_state in {OnboardingState.COLD_START_READY, OnboardingState.CONNECTIONS_PENDING}
                else "incomplete"
            )
        self.db.execute(
            """
            INSERT INTO operators (operator_id, restaurant_name, status, created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?)
            ON CONFLICT(operator_id) DO UPDATE
            SET restaurant_name = EXCLUDED.restaurant_name,
                updated_at = EXCLUDED.updated_at
            """,
            [profile.operator_id, profile.restaurant_name, now, now],
        )
        self.db.execute(
            """
            INSERT INTO operator_locations (
                operator_id, canonical_address, lat, lon, city, timezone, derived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operator_id) DO UPDATE
            SET canonical_address = EXCLUDED.canonical_address,
                lat = EXCLUDED.lat,
                lon = EXCLUDED.lon,
                city = EXCLUDED.city,
                timezone = EXCLUDED.timezone,
                derived_at = EXCLUDED.derived_at
            """,
            [
                profile.operator_id,
                profile.canonical_address,
                profile.lat,
                profile.lon,
                profile.city,
                profile.timezone,
                now,
            ],
        )
        self.db.execute(
            """
            INSERT INTO operator_service_profile (
                operator_id,
                primary_service_window,
                active_service_windows,
                demand_mix_self_declared,
                indoor_seat_capacity,
                patio_enabled,
                patio_seat_capacity,
                patio_season_mode,
                setup_mode,
                onboarding_state,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operator_id) DO UPDATE
            SET primary_service_window = EXCLUDED.primary_service_window,
                active_service_windows = EXCLUDED.active_service_windows,
                demand_mix_self_declared = EXCLUDED.demand_mix_self_declared,
                indoor_seat_capacity = EXCLUDED.indoor_seat_capacity,
                patio_enabled = EXCLUDED.patio_enabled,
                patio_seat_capacity = EXCLUDED.patio_seat_capacity,
                patio_season_mode = EXCLUDED.patio_season_mode,
                setup_mode = EXCLUDED.setup_mode,
                onboarding_state = EXCLUDED.onboarding_state,
                updated_at = EXCLUDED.updated_at
            """,
            [
                profile.operator_id,
                profile.primary_service_window.value,
                json.dumps([window.value for window in profile.active_service_windows]),
                profile.demand_mix.value,
                profile.indoor_seat_capacity,
                profile.patio_enabled,
                profile.patio_seat_capacity,
                profile.patio_season_mode,
                setup_mode,
                profile.onboarding_state.value,
                now,
            ],
        )
        self.upsert_location_context(
            LocationContextProfile(
                operator_id=profile.operator_id,
                neighborhood_archetype=profile.neighborhood_type,
            )
        )

    def upsert_weekly_baseline(
        self,
        operator_id: str,
        service_window: ServiceWindow,
        day_group: str,
        baseline_total_covers: int,
        source_type: str = "operator_setup",
        effective_from: date | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO operator_weekly_baselines (
                operator_id, service_window, day_group, baseline_total_covers, source_type, effective_from
            ) VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_DATE))
            """,
            [
                operator_id,
                service_window.value,
                day_group,
                baseline_total_covers,
                source_type,
                effective_from,
            ],
        )

    def weekly_baseline_for(self, operator_id: str, service_window: ServiceWindow, day_group: str) -> int | None:
        setup_row = self.db.fetchone(
            """
            SELECT baseline_total_covers
            FROM operator_weekly_baselines
            WHERE operator_id = ? AND service_window = ? AND day_group = ?
            ORDER BY effective_from DESC
            LIMIT 1
            """,
            [operator_id, service_window.value, day_group],
        )
        learned_row = self.db.fetchone(
            """
            SELECT baseline_mid, history_depth
            FROM baseline_learning_state
            WHERE operator_id = ? AND service_window = ? AND day_group = ?
            """,
            [operator_id, service_window.value, day_group],
        )
        setup_baseline = int(setup_row[0]) if setup_row is not None and setup_row[0] is not None else None
        if learned_row is None or learned_row[0] is None:
            return setup_baseline

        learned_mid = float(learned_row[0])
        history_depth = int(learned_row[1] or 0)
        if setup_baseline is None:
            return int(round(learned_mid))
        if history_depth < 3:
            return setup_baseline

        learned_weight = min(0.7, history_depth / 20.0)
        blended = (setup_baseline * (1.0 - learned_weight)) + (learned_mid * learned_weight)
        return int(round(blended))

    def fetch_service_profile(self, operator_id: str) -> tuple | None:
        return self.db.fetchone(
            """
            SELECT *
            FROM operator_service_profile
            WHERE operator_id = ?
            """,
            [operator_id],
        )

    def upsert_system_connection(
        self,
        *,
        operator_id: str,
        system_type: str,
        system_name: str,
        connection_state: str,
        sync_mode: str | None = None,
        field_mapping_version: str | None = None,
        truth_priority_rank: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        existing = self.db.fetchone(
            """
            SELECT connection_id
            FROM system_connections
            WHERE operator_id = ? AND system_name = ?
            """,
            [operator_id, system_name],
        )
        if existing is None:
            self.db.execute(
                """
                INSERT INTO system_connections (
                    operator_id, system_type, system_name, connection_state, sync_mode,
                    field_mapping_version, last_successful_sync_at, truth_priority_rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    operator_id,
                    system_type,
                    system_name,
                    connection_state,
                    sync_mode,
                    field_mapping_version,
                    now if connection_state == "active" else None,
                    truth_priority_rank,
                ],
            )
            return

        self.db.execute(
            """
            UPDATE system_connections
            SET system_type = ?,
                connection_state = ?,
                sync_mode = ?,
                field_mapping_version = ?,
                last_successful_sync_at = ?,
                truth_priority_rank = ?
            WHERE operator_id = ? AND system_name = ?
            """,
            [
                system_type,
                connection_state,
                sync_mode,
                field_mapping_version,
                now if connection_state == "active" else None,
                truth_priority_rank,
                operator_id,
                system_name,
            ],
        )

    def list_active_system_connections(self, operator_id: str) -> list[tuple]:
        return self.db.fetchall(
            """
            SELECT system_name, system_type, truth_priority_rank
            FROM system_connections
            WHERE operator_id = ? AND connection_state = 'active'
            ORDER BY COALESCE(truth_priority_rank, 999), system_name
            """,
            [operator_id],
        )

    def list_system_connections(self, operator_id: str) -> list[tuple]:
        return self.db.fetchall(
            """
            SELECT system_name, system_type, connection_state, sync_mode, field_mapping_version,
                   last_successful_sync_at, truth_priority_rank
            FROM system_connections
            WHERE operator_id = ?
            ORDER BY COALESCE(truth_priority_rank, 999), system_name
            """,
            [operator_id],
        )

    def upsert_location_context(self, profile: LocationContextProfile) -> None:
        now = datetime.now(UTC)
        self.db.execute(
            """
            INSERT INTO location_context_profile (
                operator_id, neighborhood_archetype, commuter_intensity, residential_intensity,
                transit_relevance, venue_relevance, hotel_travel_relevance, patio_sensitivity_hint,
                weather_sensitivity_hint, demand_volatility_hint,
                derived_at, provenance_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operator_id) DO UPDATE
            SET neighborhood_archetype = EXCLUDED.neighborhood_archetype,
                commuter_intensity = EXCLUDED.commuter_intensity,
                residential_intensity = EXCLUDED.residential_intensity,
                transit_relevance = EXCLUDED.transit_relevance,
                venue_relevance = EXCLUDED.venue_relevance,
                hotel_travel_relevance = EXCLUDED.hotel_travel_relevance,
                patio_sensitivity_hint = EXCLUDED.patio_sensitivity_hint,
                weather_sensitivity_hint = EXCLUDED.weather_sensitivity_hint,
                demand_volatility_hint = EXCLUDED.demand_volatility_hint,
                derived_at = EXCLUDED.derived_at,
                provenance_blob = EXCLUDED.provenance_blob
            """,
            [
                profile.operator_id,
                profile.neighborhood_archetype.value,
                profile.commuter_intensity,
                profile.residential_intensity,
                profile.transit_relevance,
                profile.venue_relevance,
                profile.hotel_travel_relevance,
                profile.patio_sensitivity_hint,
                profile.weather_sensitivity_hint,
                profile.demand_volatility_hint,
                now,
                json.dumps({"source": "local_scaffold"}),
            ],
        )

    def load_operator_profile(self, operator_id: str) -> OperatorProfile | None:
        row = self.db.fetchone(
            """
            SELECT o.operator_id,
                   o.restaurant_name,
                   l.canonical_address,
                   l.lat,
                   l.lon,
                   l.city,
                   l.timezone,
                   sp.primary_service_window,
                   sp.active_service_windows,
                   lcp.neighborhood_archetype,
                   sp.demand_mix_self_declared,
                   sp.indoor_seat_capacity,
                   sp.patio_enabled,
                   sp.patio_seat_capacity,
                   sp.patio_season_mode,
                   sp.setup_mode,
                   sp.onboarding_state
            FROM operators o
            LEFT JOIN operator_locations l ON l.operator_id = o.operator_id
            LEFT JOIN operator_service_profile sp ON sp.operator_id = o.operator_id
            LEFT JOIN location_context_profile lcp ON lcp.operator_id = o.operator_id
            WHERE o.operator_id = ?
            """,
            [operator_id],
        )
        if row is None:
            return None

        active_service_windows_raw = row[8]
        active_service_windows = [ServiceWindow.DINNER]
        if active_service_windows_raw:
            active_service_windows = [
                ServiceWindow(value)
                for value in json.loads(str(active_service_windows_raw))
            ]

        return OperatorProfile(
            operator_id=str(row[0]),
            restaurant_name=str(row[1]),
            canonical_address=row[2],
            lat=float(row[3]) if row[3] is not None else None,
            lon=float(row[4]) if row[4] is not None else None,
            city=row[5],
            timezone=row[6],
            primary_service_window=ServiceWindow(str(row[7])) if row[7] is not None else ServiceWindow.DINNER,
            active_service_windows=active_service_windows,
            neighborhood_type=NeighborhoodType(str(row[9])) if row[9] is not None else NeighborhoodType.MIXED_URBAN,
            demand_mix=DemandMix(str(row[10])) if row[10] is not None else DemandMix.MIXED,
            indoor_seat_capacity=int(row[11]) if row[11] is not None else None,
            patio_enabled=bool(row[12]) if row[12] is not None else False,
            patio_seat_capacity=int(row[13]) if row[13] is not None else None,
            patio_season_mode=row[14],
            setup_mode=row[15],
            onboarding_state=OnboardingState(str(row[16])) if row[16] is not None else OnboardingState.PARTIAL,
        )

    def load_location_context(self, operator_id: str) -> LocationContextProfile | None:
        row = self.db.fetchone(
            """
            SELECT operator_id, neighborhood_archetype, commuter_intensity, residential_intensity,
                   transit_relevance, venue_relevance, hotel_travel_relevance, patio_sensitivity_hint,
                   weather_sensitivity_hint, demand_volatility_hint
            FROM location_context_profile
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        if row is None:
            return None
        return LocationContextProfile(
            operator_id=str(row[0]),
            neighborhood_archetype=NeighborhoodType(str(row[1])) if row[1] is not None else NeighborhoodType.MIXED_URBAN,
            commuter_intensity=float(row[2]) if row[2] is not None else None,
            residential_intensity=float(row[3]) if row[3] is not None else None,
            transit_relevance=bool(row[4]) if row[4] is not None else False,
            venue_relevance=bool(row[5]) if row[5] is not None else False,
            hotel_travel_relevance=bool(row[6]) if row[6] is not None else False,
            patio_sensitivity_hint=float(row[7]) if row[7] is not None else None,
            weather_sensitivity_hint=float(row[8]) if row[8] is not None else None,
            demand_volatility_hint=float(row[9]) if row[9] is not None else None,
        )

    def list_active_operator_ids(self) -> list[str]:
        rows = self.db.fetchall(
            """
            SELECT operator_id
            FROM operators
            WHERE status = 'active'
              AND operator_id NOT LIKE '\\_system\\_%' ESCAPE '\\'
            ORDER BY operator_id
            """
        )
        return [str(row[0]) for row in rows]

    def replace_service_plan(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        planned_service_state: str,
        planned_total_covers: int | None,
        estimated_reduction_pct: float | None,
        raw_note: str | None,
        confirmed_by_operator: bool,
        entry_mode: str,
        review_window_start: date | None,
        review_window_end: date | None,
        updated_at: datetime,
    ) -> None:
        self.db.execute(
            """
            DELETE FROM operator_service_plan
            WHERE operator_id = ?
              AND service_date = ?
              AND service_window = ?
            """,
            [operator_id, service_date, service_window.value],
        )
        self.db.execute(
            """
            INSERT INTO operator_service_plan (
                operator_id, service_date, service_window, planned_service_state, planned_total_covers,
                estimated_reduction_pct, raw_note, confirmed_by_operator, entry_mode, review_window_start,
                review_window_end, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_date,
                service_window.value,
                planned_service_state,
                planned_total_covers,
                estimated_reduction_pct,
                raw_note,
                confirmed_by_operator,
                entry_mode,
                review_window_start,
                review_window_end,
                updated_at,
            ],
        )

    def insert_service_state_log(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        service_state: str,
        source_type: str,
        source_name: str,
        confidence: str,
        operator_confirmed: bool,
        note: str | None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO service_state_log (
                operator_id, service_date, service_window, service_state, source_type, source_name,
                confidence, operator_confirmed, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_date,
                service_window.value,
                service_state,
                source_type,
                source_name,
                confidence,
                operator_confirmed,
                note,
            ],
        )


class ForecastRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def store_published_state(self, state: PublishedForecastState) -> None:
        self.db.execute(
            """
            INSERT INTO published_forecast_state (
                operator_id,
                service_date,
                service_window,
                state_version,
                active_service_windows,
                target_name,
                forecast_expected,
                forecast_low,
                forecast_high,
                confidence_tier,
                posture,
                service_state,
                service_state_reason,
                prediction_case,
                forecast_regime,
                horizon_mode,
                top_drivers_json,
                major_uncertainties_json,
                target_definition_confidence,
                realized_total_truth_quality,
                component_truth_quality,
                resolved_source_summary_json,
                source_prediction_run_id,
                reference_status,
                reference_model,
                publish_reason,
                publish_decision,
                last_published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operator_id, service_date, service_window) DO UPDATE
            SET state_version = EXCLUDED.state_version,
                active_service_windows = EXCLUDED.active_service_windows,
                target_name = EXCLUDED.target_name,
                forecast_expected = EXCLUDED.forecast_expected,
                forecast_low = EXCLUDED.forecast_low,
                forecast_high = EXCLUDED.forecast_high,
                confidence_tier = EXCLUDED.confidence_tier,
                posture = EXCLUDED.posture,
                service_state = EXCLUDED.service_state,
                service_state_reason = EXCLUDED.service_state_reason,
                prediction_case = EXCLUDED.prediction_case,
                forecast_regime = EXCLUDED.forecast_regime,
                horizon_mode = EXCLUDED.horizon_mode,
                top_drivers_json = EXCLUDED.top_drivers_json,
                major_uncertainties_json = EXCLUDED.major_uncertainties_json,
                target_definition_confidence = EXCLUDED.target_definition_confidence,
                realized_total_truth_quality = EXCLUDED.realized_total_truth_quality,
                component_truth_quality = EXCLUDED.component_truth_quality,
                resolved_source_summary_json = EXCLUDED.resolved_source_summary_json,
                source_prediction_run_id = EXCLUDED.source_prediction_run_id,
                reference_status = EXCLUDED.reference_status,
                reference_model = EXCLUDED.reference_model,
                publish_reason = EXCLUDED.publish_reason,
                publish_decision = EXCLUDED.publish_decision,
                last_published_at = EXCLUDED.last_published_at
            """,
            [
                state.operator_id,
                state.service_date,
                state.service_window.value,
                state.state_version,
                json.dumps([window.value for window in state.active_service_windows]),
                state.target_name,
                state.forecast_expected,
                state.forecast_low,
                state.forecast_high,
                state.confidence_tier,
                state.posture,
                state.service_state.value,
                state.service_state_reason,
                state.prediction_case.value,
                state.forecast_regime.value,
                state.horizon_mode.value,
                json.dumps(state.top_drivers),
                json.dumps(state.major_uncertainties),
                state.target_definition_confidence,
                state.realized_total_truth_quality,
                state.component_truth_quality,
                json.dumps(state.resolved_source_summary),
                state.source_prediction_run_id,
                state.reference_status,
                state.reference_model,
                state.publish_reason,
                state.publish_decision.value,
                state.last_published_at,
            ],
        )

    def current_published_state(self, operator_id: str, service_date: date, service_window: ServiceWindow) -> tuple | None:
        return self.db.fetchone(
            """
            SELECT * FROM published_forecast_state
            WHERE operator_id = ? AND service_date = ? AND service_window = ?
            """,
            [operator_id, service_date, service_window.value],
        )

    def latest_working_state(self, operator_id: str, service_date: date, service_window: ServiceWindow) -> tuple | None:
        return self.db.fetchone(
            """
            SELECT * FROM working_forecast_state
            WHERE operator_id = ? AND service_date = ? AND service_window = ?
            """,
            [operator_id, service_date, service_window.value],
        )

    def insert_prediction_run(
        self,
        *,
        prediction_run_id: str,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        prediction_case: str,
        forecast_regime: str,
        horizon_mode: str,
        target_name: str,
        generator_version: str,
        context_packet_json: str,
        generated_at: datetime,
        reference_status: str | None,
        reference_model: str | None,
        reference_details_json: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO prediction_runs (
                prediction_run_id, operator_id, service_date, service_window, prediction_case, forecast_regime,
                horizon_mode, target_name, generator_version, context_packet_json, generated_at,
                reference_status, reference_model, reference_details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                prediction_run_id,
                operator_id,
                service_date,
                service_window.value,
                prediction_case,
                forecast_regime,
                horizon_mode,
                target_name,
                generator_version,
                context_packet_json,
                generated_at,
                reference_status,
                reference_model,
                reference_details_json,
            ],
        )

    def insert_prediction_components(
        self,
        component_rows: list[tuple[str, str, str, int | None, bool, bool]],
    ) -> None:
        for row in component_rows:
            self.db.execute(
                """
                INSERT INTO prediction_components (
                    prediction_run_id, component_name, component_state, predicted_value,
                    is_operator_visible, is_learning_eligible
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                list(row),
            )

    def upsert_engine_digest(
        self,
        *,
        prediction_run_id: str,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        digest_json: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO engine_digest (prediction_run_id, operator_id, service_date, service_window, digest_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(prediction_run_id) DO UPDATE SET digest_json = EXCLUDED.digest_json
            """,
            [
                prediction_run_id,
                operator_id,
                service_date,
                service_window.value,
                digest_json,
            ],
        )

    def fetch_engine_digest(self, prediction_run_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT digest_json
            FROM engine_digest
            WHERE prediction_run_id = ?
            """,
            [prediction_run_id],
        )
        if row is None or row[0] is None:
            return None
        try:
            parsed = json.loads(str(row[0]))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def upsert_working_forecast_state(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        target_name: str,
        forecast_expected: int,
        forecast_low: int,
        forecast_high: int,
        confidence_tier: str,
        posture: str,
        service_state: str,
        source_prediction_run_id: str,
        reference_status: str | None,
        reference_model: str | None,
        refresh_reason: str,
        refreshed_at: datetime,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO working_forecast_state (
                operator_id, service_date, service_window, target_name, forecast_expected, forecast_low,
                forecast_high, confidence_tier, posture, service_state, source_prediction_run_id,
                reference_status, reference_model, refresh_reason, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operator_id, service_date, service_window) DO UPDATE
            SET target_name = EXCLUDED.target_name,
                forecast_expected = EXCLUDED.forecast_expected,
                forecast_low = EXCLUDED.forecast_low,
                forecast_high = EXCLUDED.forecast_high,
                confidence_tier = EXCLUDED.confidence_tier,
                posture = EXCLUDED.posture,
                service_state = EXCLUDED.service_state,
                source_prediction_run_id = EXCLUDED.source_prediction_run_id,
                reference_status = EXCLUDED.reference_status,
                reference_model = EXCLUDED.reference_model,
                refresh_reason = EXCLUDED.refresh_reason,
                refreshed_at = EXCLUDED.refreshed_at
            """,
            [
                operator_id,
                service_date,
                service_window.value,
                target_name,
                forecast_expected,
                forecast_low,
                forecast_high,
                confidence_tier,
                posture,
                service_state,
                source_prediction_run_id,
                reference_status,
                reference_model,
                refresh_reason,
                refreshed_at,
            ],
        )

    def insert_forecast_publication_snapshot(
        self,
        *,
        operator_id: str,
        service_date: date,
        service_window: ServiceWindow,
        state_version: int,
        target_name: str,
        forecast_expected: int,
        forecast_low: int,
        forecast_high: int,
        confidence_tier: str,
        posture: str,
        service_state: str,
        source_prediction_run_id: str,
        reference_status: str | None,
        reference_model: str | None,
        snapshot_reason: str,
        snapshot_at: datetime,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO forecast_publication_snapshots (
                operator_id, service_date, service_window, state_version, target_name, forecast_expected,
                forecast_low, forecast_high, confidence_tier, posture, service_state, source_prediction_run_id,
                reference_status, reference_model, snapshot_reason, snapshot_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                service_date,
                service_window.value,
                state_version,
                target_name,
                forecast_expected,
                forecast_low,
                forecast_high,
                confidence_tier,
                posture,
                service_state,
                source_prediction_run_id,
                reference_status,
                reference_model,
                snapshot_reason,
                snapshot_at,
            ],
        )


class AgentFrameworkRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_agent_run_log(
        self,
        *,
        run_id: str,
        role: str,
        operator_id: str,
        status: str,
        triggered_at: datetime,
        tokens_used: int,
        outputs_count: int,
        error: str | None,
        blocked_reason: str | None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO agent_run_log (
                run_id, role, operator_id, status, triggered_at, tokens_used,
                outputs_count, error, blocked_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                role,
                operator_id,
                status,
                _db_timestamp(triggered_at),
                tokens_used,
                outputs_count,
                error,
                blocked_reason,
            ],
        )

    def insert_agent_derived_external_signal(
        self,
        *,
        operator_id: str,
        signal_id: str,
        signal: dict[str, Any],
        status: str,
        origin_agent: str,
        source_prediction_run_id: str | None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO external_signal_log (
                signal_id, operator_id, signal_type, source_name, source_class,
                dependency_group, source_bucket, scan_scope, start_time, end_time,
                service_window_overlap, trust_level, direction, strength,
                recommended_role, details_json, source_prediction_run_id,
                status, origin_agent, staged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'proposed' THEN CURRENT_TIMESTAMP ELSE NULL END)
            """,
            [
                signal_id,
                operator_id,
                str(signal.get("category") or "agent_signal"),
                str(signal.get("source_name") or origin_agent),
                "agent_signal",
                str(signal.get("dependency_group") or "local_context"),
                str(signal.get("source_bucket") or "broad_proxy"),
                signal.get("scan_scope"),
                None,
                None,
                1.0,
                str(signal.get("trust_level") or "medium"),
                str(signal.get("direction") or "neutral"),
                abs(float(signal.get("strength") or 0.0)),
                str(signal.get("role") or "confidence_mover"),
                json.dumps(signal, default=str),
                source_prediction_run_id,
                status,
                origin_agent,
                status,
            ],
        )

    def fetch_recent_notes_for_window(
        self,
        *,
        operator_id: str,
        start_date: date,
        end_date: date,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT service_date, service_window, raw_note,
                   suggested_service_state, suggested_correction_json, created_at
            FROM conversation_note_log
            WHERE operator_id = ?
              AND service_date BETWEEN ? AND ?
            ORDER BY service_date DESC, created_at DESC, note_id DESC
            LIMIT ?
            """,
            [operator_id, start_date, end_date, limit],
        )
        return [
            {
                "service_date": row[0],
                "service_window": row[1],
                "raw_note": row[2],
                "suggested_service_state": row[3],
                "suggested_correction_json": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def fetch_open_hypotheses_compact(
        self,
        *,
        operator_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """
            SELECT hypothesis_key, hypothesis_value_json, status, confidence, last_triggered_at
            FROM operator_hypothesis_state
            WHERE operator_id = ?
              AND status = 'open'
            ORDER BY last_triggered_at DESC
            LIMIT ?
            """,
            [operator_id, limit],
        )
        compact: list[dict[str, Any]] = []
        for row in rows:
            proposition = None
            if row[1] is not None:
                try:
                    parsed = json.loads(str(row[1]))
                except json.JSONDecodeError:
                    parsed = {}
                if isinstance(parsed, dict):
                    proposition = parsed.get("proposition")
            compact.append(
                {
                    "hypothesis_key": row[0],
                    "proposition": proposition,
                    "status": row[2],
                    "confidence": row[3],
                    "last_triggered_at": row[4],
                }
            )
        return compact

    def insert_anomaly_hypothesis(
        self,
        *,
        operator_id: str,
        hypothesis: dict[str, Any],
    ) -> None:
        hypothesis_value = {
            "category": hypothesis.get("category"),
            "proposition": hypothesis.get("proposition"),
            "dependency_group": hypothesis.get("dependency_group"),
            "origin": hypothesis.get("origin") or "anomaly_explainer",
        }
        evidence = {
            "evidence": hypothesis.get("evidence"),
            "origin": "anomaly_explainer",
            "trigger_error_pct": hypothesis.get("trigger_error_pct"),
            "trigger_run_id": hypothesis.get("trigger_run_id"),
        }
        self.db.execute(
            """
            INSERT OR IGNORE INTO operator_hypothesis_state (
                operator_id, hypothesis_key, status, confidence,
                hypothesis_value_json, evidence_json
            ) VALUES (?, ?, 'open', ?, ?, ?)
            """,
            [
                operator_id,
                str(hypothesis.get("hypothesis_key") or ""),
                str(hypothesis.get("confidence") or "low"),
                json.dumps(hypothesis_value, default=str),
                json.dumps(evidence, default=str),
            ],
        )


class OperatorContextDigestRepository:
    """Reads/writes `operator_context_digest` — the typed digest cache that the
    conversation orchestrator reads at chat-turn time. Retrievers write rows on
    event (refresh, actual, note, hypothesis); the orchestrator never triggers
    a retriever.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._max_rows_per_operator_kind = 100

    def insert_digest(
        self,
        *,
        operator_id: str,
        kind: str,
        produced_at: datetime,
        source_hash: str,
        payload_json: str,
        agent_run_id: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO operator_context_digest (
                operator_id, kind, produced_at, source_hash, agent_run_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                kind,
                _db_timestamp(produced_at),
                source_hash,
                agent_run_id,
                payload_json,
            ],
        )
        self._prune_old_digests(operator_id=operator_id, kind=kind)

    def _prune_old_digests(self, *, operator_id: str, kind: str) -> None:
        self.db.execute(
            """
            DELETE FROM operator_context_digest
            WHERE operator_id = ?
              AND kind = ?
              AND produced_at IN (
                SELECT produced_at
                FROM (
                    SELECT
                        produced_at,
                        ROW_NUMBER() OVER (ORDER BY produced_at DESC) AS row_number
                    FROM operator_context_digest
                    WHERE operator_id = ?
                      AND kind = ?
                )
                WHERE row_number > ?
              )
            """,
            [
                operator_id,
                kind,
                operator_id,
                kind,
                self._max_rows_per_operator_kind,
            ],
        )

    def fetch_latest(
        self,
        *,
        operator_id: str,
        kind: str,
    ) -> dict[str, Any] | None:
        row = self.db.fetchone(
            """
            SELECT produced_at, source_hash, agent_run_id, payload_json
            FROM operator_context_digest
            WHERE operator_id = ? AND kind = ?
            ORDER BY produced_at DESC
            LIMIT 1
            """,
            [operator_id, kind],
        )
        if row is None:
            return None
        try:
            payload = json.loads(str(row[3])) if row[3] is not None else {}
        except json.JSONDecodeError:
            payload = {}
        return {
            "produced_at": row[0],
            "source_hash": row[1],
            "agent_run_id": row[2],
            "payload": payload,
        }


class ConversationMemoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_fact(
        self,
        *,
        operator_id: str,
        fact_key: str,
        fact_value_json: str,
        confidence: str,
        provenance: str,
        source_ref: str | None = None,
        valid_from_date: date | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        existing = self.db.fetchone(
            "SELECT fact_id FROM operator_fact_memory WHERE operator_id = ? AND fact_key = ?",
            [operator_id, fact_key],
        )
        if existing is None:
            self.db.execute(
                """
                INSERT INTO operator_fact_memory (
                    operator_id, fact_key, fact_value_json, confidence, provenance, source_ref,
                    valid_from_date, expires_at, status, last_confirmed_at, created_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                [
                    operator_id,
                    fact_key,
                    fact_value_json,
                    confidence,
                    provenance,
                    source_ref,
                    valid_from_date,
                    expires_at,
                    _db_timestamp(),
                    _db_timestamp(),
                    _db_timestamp(),
                ],
            )
            return
        self.db.execute(
            """
            UPDATE operator_fact_memory
            SET fact_value_json = ?,
                confidence = ?,
                provenance = ?,
                source_ref = ?,
                valid_from_date = ?,
                expires_at = ?,
                status = 'active',
                last_confirmed_at = ?,
                last_updated_at = ?
            WHERE operator_id = ? AND fact_key = ?
            """,
            [
                fact_value_json,
                confidence,
                provenance,
                source_ref,
                valid_from_date,
                expires_at,
                _db_timestamp(),
                _db_timestamp(),
                operator_id,
                fact_key,
            ],
        )
