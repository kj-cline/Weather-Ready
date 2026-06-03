from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from stormready_v3.domain.enums import RefreshReason, ServiceWindow
from stormready_v3.domain.models import LocationContextProfile
from stormready_v3.mvp_scope import is_runtime_window_supported
from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator


SCHEDULE_WINDOW_HOURS: dict[str, tuple[int, int]] = {
    "morning": (6, 11),
    "midday": (11, 15),
    "pre_dinner": (15, 20),
}

EVENT_MODE_LOOKBACK_HOURS = 6
EVENT_MODE_COOLDOWN_MINUTES = 60
SIGNAL_MONITOR_STALENESS_MINUTES = 30
SIGNAL_PREFETCH_LEAD_HOURS = 1
CATALOG_DISCOVERY_STALENESS_HOURS = 3


@dataclass(slots=True)
class SupervisorTickResult:
    started_at: datetime
    completed_at: datetime | None = None
    processed_operator_ids: list[str] = field(default_factory=list)
    queued_requests_completed: int = 0
    signal_monitor_runs: int = 0
    scheduled_runs: int = 0
    event_mode_runs: int = 0
    skipped_due_to_recent_run: int = 0


class SupervisorService:
    def __init__(self, orchestrator: DeterministicOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.db = orchestrator.db

    @staticmethod
    def _db_timestamp(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def enqueue_operator_refresh_request(
        self,
        *,
        operator_id: str,
        requested_for_date: date | None = None,
        requested_service_window: ServiceWindow | None = None,
        note: str | None = None,
    ) -> int:
        self.db.execute(
            """
            INSERT INTO refresh_request_queue (
                operator_id, requested_reason, requested_for_date, requested_service_window, note
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                operator_id,
                RefreshReason.OPERATOR_REQUESTED.value,
                requested_for_date,
                requested_service_window.value if requested_service_window is not None else None,
                note,
            ],
        )
        row = self.db.fetchone("SELECT max(request_id) FROM refresh_request_queue")
        return int(row[0])

    def run_tick(
        self,
        *,
        now: datetime | None = None,
        process_queue: bool = True,
        process_scheduled: bool = True,
        process_event_mode: bool = True,
    ) -> SupervisorTickResult:
        now = now or datetime.now(UTC)
        tick_id = self._start_tick(now)
        result = SupervisorTickResult(started_at=now)
        processed_this_tick: set[str] = set()

        try:
            if process_queue:
                for request in self._list_pending_requests():
                    operator_id = str(request[1])
                    if self._process_request_row(request, now=now):
                        result.queued_requests_completed += 1
                        result.processed_operator_ids.append(operator_id)
                        processed_this_tick.add(operator_id)

            for operator_id in self.orchestrator.operators.list_active_operator_ids():
                profile = self.orchestrator.operators.load_operator_profile(operator_id)
                if profile is None:
                    continue
                location_context = self.orchestrator.operators.load_location_context(operator_id) or LocationContextProfile(
                    operator_id=operator_id,
                    neighborhood_archetype=profile.neighborhood_type,
                )
                local_now = self._local_now_for_profile(profile, now)
                if self._refresh_in_progress(operator_id=operator_id, now=now):
                    result.skipped_due_to_recent_run += 1
                    continue
                if process_scheduled or process_event_mode:
                    signal_monitor_due, prefetch_window = self._signal_monitor_due(
                        operator_id=operator_id,
                        now=now,
                        local_now=local_now,
                    )
                    if signal_monitor_due:
                        self._run_signal_monitor(
                            profile=profile,
                            location_context=location_context,
                            now=now,
                            local_now=local_now,
                            prefetch_window=prefetch_window,
                            run_catalog_discovery=self._catalog_discovery_due(
                                operator_id=operator_id,
                                now=now,
                                prefetch_window=prefetch_window,
                            ),
                        )
                        result.signal_monitor_runs += 1
                if process_scheduled:
                    refresh_window = self._scheduled_window_for_local_time(local_now)
                    if refresh_window and operator_id not in processed_this_tick:
                        if self._scheduled_run_due(operator_id=operator_id, run_date=local_now.date(), refresh_window=refresh_window):
                            self.orchestrator.run_refresh_cycle(
                                profile=profile,
                                location_context=location_context,
                                refresh_reason=RefreshReason.SCHEDULED,
                                run_date=local_now.date(),
                                refresh_window=refresh_window,
                                executed_at=now,
                            )
                            result.scheduled_runs += 1
                            result.processed_operator_ids.append(operator_id)
                            processed_this_tick.add(operator_id)
                        else:
                            result.skipped_due_to_recent_run += 1

                if process_event_mode and operator_id not in processed_this_tick:
                    if self._event_mode_due(operator_id=operator_id, now=now):
                        self.orchestrator.run_refresh_cycle(
                            profile=profile,
                            location_context=location_context,
                            refresh_reason=RefreshReason.EVENT_MODE,
                            run_date=local_now.date(),
                            event_mode_active=True,
                            executed_at=now,
                        )
                        result.event_mode_runs += 1
                        result.processed_operator_ids.append(operator_id)
                        processed_this_tick.add(operator_id)

            result.completed_at = now
            self._complete_tick(tick_id, result, status="completed")
            return result
        except Exception as exc:
            result.completed_at = now
            self._complete_tick(tick_id, result, status="failed", failure_reason=str(exc))
            raise

    def run_operator_tick(
        self,
        *,
        operator_id: str,
        now: datetime | None = None,
        process_queue: bool = True,
        process_scheduled: bool = True,
        process_event_mode: bool = True,
    ) -> SupervisorTickResult:
        now = now or datetime.now(UTC)
        tick_id = self._start_tick(now)
        result = SupervisorTickResult(started_at=now)

        try:
            profile = self.orchestrator.operators.load_operator_profile(operator_id)
            if profile is None:
                result.completed_at = now
                self._complete_tick(tick_id, result, status="completed")
                return result

            location_context = self.orchestrator.operators.load_location_context(operator_id) or LocationContextProfile(
                operator_id=operator_id,
                neighborhood_archetype=profile.neighborhood_type,
            )
            local_now = self._local_now_for_profile(profile, now)
            processed = False

            if self._refresh_in_progress(operator_id=operator_id, now=now):
                result.skipped_due_to_recent_run += 1
                result.completed_at = now
                self._complete_tick(tick_id, result, status="completed")
                return result

            if process_scheduled or process_event_mode:
                signal_monitor_due, prefetch_window = self._signal_monitor_due(
                    operator_id=operator_id,
                    now=now,
                    local_now=local_now,
                )
                if signal_monitor_due:
                    self._run_signal_monitor(
                        profile=profile,
                        location_context=location_context,
                        now=now,
                        local_now=local_now,
                        prefetch_window=prefetch_window,
                        run_catalog_discovery=self._catalog_discovery_due(
                            operator_id=operator_id,
                            now=now,
                            prefetch_window=prefetch_window,
                        ),
                    )
                    result.signal_monitor_runs += 1

            if process_queue:
                for request in self._list_pending_requests_for_operator(operator_id):
                    if self._process_request_row(request, now=now):
                        result.queued_requests_completed += 1
                        result.processed_operator_ids.append(operator_id)
                        processed = True

            if process_scheduled and not processed:
                refresh_window = self._scheduled_window_for_local_time(local_now)
                if refresh_window:
                    if self._scheduled_run_due(
                        operator_id=operator_id,
                        run_date=local_now.date(),
                        refresh_window=refresh_window,
                    ):
                        self.orchestrator.run_refresh_cycle(
                            profile=profile,
                            location_context=location_context,
                            refresh_reason=RefreshReason.SCHEDULED,
                            run_date=local_now.date(),
                            refresh_window=refresh_window,
                            executed_at=now,
                        )
                        result.scheduled_runs += 1
                        result.processed_operator_ids.append(operator_id)
                        processed = True
                    else:
                        result.skipped_due_to_recent_run += 1

            if process_event_mode and not processed:
                if self._event_mode_due(operator_id=operator_id, now=now):
                    self.orchestrator.run_refresh_cycle(
                        profile=profile,
                        location_context=location_context,
                        refresh_reason=RefreshReason.EVENT_MODE,
                        run_date=local_now.date(),
                        event_mode_active=True,
                        executed_at=now,
                    )
                    result.event_mode_runs += 1
                    result.processed_operator_ids.append(operator_id)

            result.completed_at = now
            self._complete_tick(tick_id, result, status="completed")
            return result
        except Exception as exc:
            result.completed_at = now
            self._complete_tick(tick_id, result, status="failed", failure_reason=str(exc))
            raise

    def _start_tick(self, started_at: datetime) -> int:
        self.db.execute(
            """
            INSERT INTO supervisor_tick_log (
                started_at, tick_mode, run_date, summary_json, status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [self._db_timestamp(started_at), "auto", started_at.date(), json.dumps({}), "running"],
        )
        row = self.db.fetchone("SELECT max(tick_id) FROM supervisor_tick_log")
        return int(row[0])

    def _complete_tick(
        self,
        tick_id: int,
        result: SupervisorTickResult,
        *,
        status: str,
        failure_reason: str | None = None,
    ) -> None:
        self.db.execute(
            """
            UPDATE supervisor_tick_log
            SET completed_at = ?, summary_json = ?, status = ?, failure_reason = ?
            WHERE tick_id = ?
            """,
            [
                self._db_timestamp(result.completed_at),
                json.dumps(
                    {
                        "processed_operator_ids": result.processed_operator_ids,
                        "queued_requests_completed": result.queued_requests_completed,
                        "signal_monitor_runs": result.signal_monitor_runs,
                        "scheduled_runs": result.scheduled_runs,
                        "event_mode_runs": result.event_mode_runs,
                        "skipped_due_to_recent_run": result.skipped_due_to_recent_run,
                    }
                ),
                status,
                failure_reason,
                tick_id,
            ],
        )

    def _list_pending_requests(self) -> list[tuple]:
        return self.db.fetchall(
            """
            SELECT request_id, operator_id, requested_for_date, requested_service_window, note
            FROM refresh_request_queue
            WHERE status = 'pending'
            ORDER BY requested_at ASC
            """
        )

    def _list_pending_requests_for_operator(self, operator_id: str) -> list[tuple]:
        return self.db.fetchall(
            """
            SELECT request_id, operator_id, requested_for_date, requested_service_window, note
            FROM refresh_request_queue
            WHERE status = 'pending'
              AND operator_id = ?
            ORDER BY requested_at ASC
            """,
            [operator_id],
        )

    def _process_request_row(self, row: tuple, *, now: datetime) -> bool:
        request_id = int(row[0])
        operator_id = str(row[1])
        requested_for_date = row[2]
        requested_service_window = row[3]
        if self._refresh_in_progress(operator_id=operator_id, now=now):
            return False
        profile = self.orchestrator.operators.load_operator_profile(operator_id)
        if profile is None:
            self.db.execute(
                """
                UPDATE refresh_request_queue
                SET status = 'failed', failed_at = ?, failure_reason = ?
                WHERE request_id = ?
                """,
                [self._db_timestamp(now), "operator_not_found", request_id],
            )
            return False

        location_context = self.orchestrator.operators.load_location_context(operator_id) or LocationContextProfile(
            operator_id=operator_id,
            neighborhood_archetype=profile.neighborhood_type,
        )
        service_windows = None
        if requested_service_window:
            normalized_window = ServiceWindow(str(requested_service_window))
            if is_runtime_window_supported(normalized_window):
                service_windows = [normalized_window]
        run_date = requested_for_date or self._local_now_for_profile(profile, now).date()
        claim_time = self._db_timestamp(now)
        self.db.execute(
            """
            UPDATE refresh_request_queue
            SET status = 'processing', claimed_at = ?
            WHERE request_id = ?
              AND status = 'pending'
            """,
            [claim_time, request_id],
        )
        claimed_row = self.db.fetchone(
            """
            SELECT status, claimed_at
            FROM refresh_request_queue
            WHERE request_id = ?
            """,
            [request_id],
        )
        if claimed_row is None or str(claimed_row[0]) != "processing" or claimed_row[1] != claim_time:
            return False
        self.orchestrator.run_refresh_cycle(
            profile=profile,
            location_context=location_context,
            refresh_reason=RefreshReason.OPERATOR_REQUESTED,
            run_date=run_date,
            service_windows=service_windows,
            executed_at=now,
        )
        self.db.execute(
            """
            UPDATE refresh_request_queue
            SET status = 'completed', completed_at = ?
            WHERE request_id = ?
            """,
            [self._db_timestamp(now), request_id],
        )
        return True

    def _local_now_for_profile(self, profile, now: datetime) -> datetime:
        timezone = profile.timezone or "America/New_York"
        return now.astimezone(ZoneInfo(timezone))

    def _scheduled_window_for_local_time(self, local_now: datetime) -> str | None:
        hour = local_now.hour
        for window_name, (start_hour, end_hour) in SCHEDULE_WINDOW_HOURS.items():
            if start_hour <= hour < end_hour:
                return window_name
        return None

    def _scheduled_run_due(self, *, operator_id: str, run_date: date, refresh_window: str) -> bool:
        row = self.db.fetchone(
            """
            SELECT refresh_run_id
            FROM forecast_refresh_runs
            WHERE operator_id = ?
              AND refresh_reason = ?
              AND run_date = ?
              AND refresh_window = ?
              AND status IN ('running', 'completed')
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            [operator_id, RefreshReason.SCHEDULED.value, run_date, refresh_window],
        )
        return row is None

    def _refresh_in_progress(self, *, operator_id: str, now: datetime) -> bool:
        row = self.db.fetchone(
            """
            SELECT refresh_run_id
            FROM forecast_refresh_runs
            WHERE operator_id = ?
              AND status = 'running'
              AND started_at >= ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            [operator_id, self._db_timestamp(now - timedelta(hours=6))],
        )
        return row is not None

    def _latest_signal_monitor_activity(self, *, operator_id: str) -> datetime | None:
        candidates = self.db.fetchone(
            """
            WITH signal_activity AS (
                SELECT MAX(created_at) AS ts
                FROM external_signal_log
                WHERE operator_id = ?
            ),
            weather_activity AS (
                SELECT MAX(retrieved_at) AS ts
                FROM weather_pulls
                WHERE operator_id = ?
            ),
            catalog_activity AS (
                SELECT MAX(scanned_at) AS ts
                FROM external_scan_run_log
                WHERE operator_id = ?
            )
            SELECT GREATEST(
                COALESCE((SELECT ts FROM signal_activity), TIMESTAMP '1970-01-01 00:00:00'),
                COALESCE((SELECT ts FROM weather_activity), TIMESTAMP '1970-01-01 00:00:00'),
                COALESCE((SELECT ts FROM catalog_activity), TIMESTAMP '1970-01-01 00:00:00')
            )
            """,
            [operator_id, operator_id, operator_id],
        )
        if candidates is None or candidates[0] is None:
            return None
        latest_activity = candidates[0]
        if latest_activity.year <= 1970:
            return None
        if latest_activity.tzinfo is None:
            return latest_activity.replace(tzinfo=UTC)
        return latest_activity.astimezone(UTC)

    def _signal_monitor_due(
        self,
        *,
        operator_id: str,
        now: datetime,
        local_now: datetime,
    ) -> tuple[bool, str | None]:
        prefetch_window = self._prefetch_window_for_local_time(local_now)
        last_activity = self._latest_signal_monitor_activity(operator_id=operator_id)

        if prefetch_window is not None:
            if last_activity is None or last_activity <= now - timedelta(minutes=15):
                return True, prefetch_window
            return False, prefetch_window

        if last_activity is None:
            return True, None
        return last_activity <= now - timedelta(minutes=SIGNAL_MONITOR_STALENESS_MINUTES), None

    def _catalog_discovery_due(
        self,
        *,
        operator_id: str,
        now: datetime,
        prefetch_window: str | None,
    ) -> bool:
        if prefetch_window is not None:
            return True
        row = self.db.fetchone(
            """
            SELECT MAX(scanned_at)
            FROM external_scan_run_log
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        last_scan = row[0] if row and row[0] is not None else None
        if last_scan is None:
            return True
        if last_scan.tzinfo is None:
            last_scan = last_scan.replace(tzinfo=UTC)
        return last_scan <= now - timedelta(hours=CATALOG_DISCOVERY_STALENESS_HOURS)

    def _prefetch_window_for_local_time(self, local_now: datetime) -> str | None:
        hour = local_now.hour
        for window_name, (start_hour, _end_hour) in SCHEDULE_WINDOW_HOURS.items():
            if start_hour - SIGNAL_PREFETCH_LEAD_HOURS <= hour < start_hour:
                return window_name
        return None

    def _run_signal_monitor(
        self,
        *,
        profile,
        location_context: LocationContextProfile,
        now: datetime,
        local_now: datetime,
        prefetch_window: str | None,
        run_catalog_discovery: bool,
    ) -> None:
        service_window = profile.primary_service_window
        if not is_runtime_window_supported(service_window):
            return

        refresh_reason = RefreshReason.SCHEDULED if prefetch_window is not None else RefreshReason.EVENT_MODE
        if run_catalog_discovery:
            self.orchestrator.external_catalog.run_refresh_discovery(
                profile=profile,
                location_context=location_context,
                run_date=local_now.date(),
                refresh_reason=refresh_reason,
                refresh_window=prefetch_window,
                scanned_at=now,
            )

        payloads, _failures = self.orchestrator.fetch_source_payloads(
            profile=profile,
            service_date=local_now.date(),
            service_window=service_window,
            fetched_at=now,
        )
        normalized_signals = self.orchestrator.normalize_source_payloads(payloads)
        signal_scan_id = f"signal_scan:{profile.operator_id}:{now.astimezone(UTC).strftime('%Y%m%dT%H%M%S')}"
        self.orchestrator.persist_source_evidence(
            operator_id=profile.operator_id,
            service_date=local_now.date(),
            service_window=service_window,
            prediction_run_id=signal_scan_id,
            payloads=payloads,
            normalized_signals=normalized_signals,
        )

    def _event_mode_due(self, *, operator_id: str, now: datetime) -> bool:
        active_signal = self.db.fetchone(
            """
            SELECT signal_id
            FROM external_signal_log
            WHERE operator_id = ?
              AND created_at >= ?
              AND (
                    recommended_role = 'service_state_modifier'
                 OR signal_type IN ('weather_alert', 'weather_disruption_risk', 'snow_risk', 'nws_active_alert')
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [operator_id, now - timedelta(hours=EVENT_MODE_LOOKBACK_HOURS)],
        )
        if active_signal is None:
            return False

        recent_event_refresh = self.db.fetchone(
            """
            SELECT completed_at
            FROM forecast_refresh_runs
            WHERE operator_id = ?
              AND refresh_reason = ?
              AND status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            [operator_id, RefreshReason.EVENT_MODE.value],
        )
        if recent_event_refresh is None or recent_event_refresh[0] is None:
            return True
        completed_at = recent_event_refresh[0]
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=UTC)
        return completed_at <= now - timedelta(minutes=EVENT_MODE_COOLDOWN_MINUTES)
