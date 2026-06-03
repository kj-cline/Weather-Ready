from __future__ import annotations

from dataclasses import asdict
import json
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stormready_v3.agents.tools import ToolExecutor
from stormready_v3.agents.unified import UnifiedAgentService, _load_message_page, _load_recent_history, detect_phase
from stormready_v3.ai.factory import build_agent_model_provider
from stormready_v3.config.settings import ACTIONABLE_HORIZON_DAYS, RUNTIME_DATA_ROOT, background_supervisor_enabled
from stormready_v3.conversation.service import ConversationContextService
from stormready_v3.domain.enums import DemandMix, NeighborhoodType, ServiceState, ServiceWindow
from stormready_v3.imports.history_upload import (
    claim_historical_upload_for_operator,
    load_operator_historical_upload_review,
    load_staged_historical_upload,
    review_and_stage_historical_upload,
)
from stormready_v3.operator_text import (
    driver_label,
    forecast_recent_change_text,
    forecast_vs_usual_text,
    operator_text_contract,
    planning_band,
    service_state_label,
    translate_operator_text,
)
from stormready_v3.runtime_bridge import (
    enrich_cards_with_deltas,
    load_pending_notifications,
    load_snapshot_deltas,
    mark_notifications_delivered,
    notifications_to_chat_message,
)
from stormready_v3.reference.operator_history import (
    clear_operator_reference_selection,
    train_operator_history_reference_asset,
)
from stormready_v3.setup.readiness import summarize_setup_readiness
from stormready_v3.sources.weather_archive import (
    BROOKLYN_SYSTEM_OPERATOR_ID,
    ensure_brooklyn_baseline,
    ensure_operator_weather_baseline,
)
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository
from stormready_v3.surfaces.operator_state_packet import OperatorStatePacketService, resolve_operator_current_time
from stormready_v3.surfaces.workflow_state import load_missing_actuals, load_service_plan_window, service_plan_review_window
from stormready_v3.workflows.actuals import record_actual_total_and_update
from stormready_v3.workflows.setup_context_digests import ensure_setup_context_digests

from .serializers import serialize_value

if TYPE_CHECKING:
    from stormready_v3.agents.base import AgentDispatcher


ONBOARDING_TIMEZONES = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "Pacific/Honolulu",
)
PATIO_SEASON_MODES = ("seasonal", "year_round", "winter_enclosed")
ONBOARDING_BASELINE_KEYS = ("mon_thu", "fri", "sat", "sun")
FORECAST_INPUT_MODES = ("manual_baselines", "historical_upload")
ACTIVE_SETUP_BOOTSTRAP_STATUSES = {"pending", "running"}
TERMINAL_SETUP_BOOTSTRAP_STATUSES = {"completed", "failed"}
_SETUP_BOOTSTRAP_THREADS: dict[str, threading.Thread] = {}
_SETUP_BOOTSTRAP_LOCK = threading.Lock()


def onboarding_options() -> dict[str, Any]:
    return {
        "timezones": list(ONBOARDING_TIMEZONES),
        "demandMixOptions": [
            {"value": DemandMix.WALK_IN_LED.value, "label": "Mostly walk-ins"},
            {"value": DemandMix.MIXED.value, "label": "Mixed"},
            {"value": DemandMix.RESERVATION_LED.value, "label": "Mostly reservations"},
        ],
        "neighborhoodOptions": [
            {"value": NeighborhoodType.OFFICE_HEAVY.value, "label": "Office district"},
            {"value": NeighborhoodType.RESIDENTIAL.value, "label": "Residential"},
            {"value": NeighborhoodType.MIXED_URBAN.value, "label": "Mixed urban"},
            {"value": NeighborhoodType.DESTINATION_NIGHTLIFE.value, "label": "Nightlife / destination"},
            {"value": NeighborhoodType.TRAVEL_HOTEL_STATION.value, "label": "Hotel / transit hub"},
        ],
        "patioSeasonModes": list(PATIO_SEASON_MODES),
        "forecastInputModes": [
            {
                "value": "manual_baselines",
                "label": "Enter baselines",
                "description": "Enter typical Mon-Thu, Fri, Sat, and Sun dinner covers by hand.",
            },
            {
                "value": "historical_upload",
                "label": "Upload 12 months",
                "description": "Upload at least 12 months of cover history and let the system derive baselines.",
            },
        ],
        "historicalUploadRequirements": [
            "CSV file with a service date column and a total covers column.",
            "At least 12 months of usable dinner history across all four seasons.",
            "Location must be set so the same-date weather history can be attached.",
            "Reserved covers, patio/outside covers, and service-state columns are optional.",
        ],
        "historicalUploadAcceptedExtensions": [".csv", ".txt"],
    }


def empty_onboarding_draft() -> dict[str, Any]:
    return {
        "restaurantName": "",
        "canonicalAddress": "",
        "city": "",
        "timezone": ONBOARDING_TIMEZONES[0],
        "forecastInputMode": FORECAST_INPUT_MODES[0],
        "historicalUploadToken": None,
        "historicalUploadReview": None,
        "monThu": 0,
        "fri": 0,
        "sat": 0,
        "sun": 0,
        "demandMix": DemandMix.MIXED.value,
        "neighborhoodType": NeighborhoodType.MIXED_URBAN.value,
        "patioEnabled": False,
        "patioSeatCapacity": 0,
        "patioSeasonMode": PATIO_SEASON_MODES[0],
        "transitRelevance": False,
        "venueRelevance": False,
        "hotelTravelRelevance": False,
    }


def _db_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _persist_notification_message(
    db: Database,
    *,
    operator_id: str,
    content: str,
    created_at: datetime,
) -> None:
    normalized = translate_operator_text(" ".join(str(content or "").split()).strip())
    if not normalized:
        return
    existing = db.fetchone(
        """
        SELECT message_id
        FROM conversation_messages
        WHERE operator_id = ?
          AND role = 'assistant'
          AND content = ?
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [operator_id, normalized, created_at - timedelta(minutes=1)],
    )
    if existing is not None:
        return
    db.execute(
        """
        INSERT INTO conversation_messages (
            operator_id, role, content, tool_results_json, phase, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            operator_id,
            "assistant",
            normalized,
            json.dumps({"source": "notification"}),
            "operations",
            created_at,
        ],
    )


def _resolve_reference_date(raw_value: Any) -> date:
    if raw_value is None or raw_value == "":
        return date.today()
    if isinstance(raw_value, date):
        return raw_value
    return date.fromisoformat(str(raw_value))


def _background_supervisor_enabled() -> bool:
    return background_supervisor_enabled()


def _parse_step_list(raw_value: Any) -> list[str]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _card_lookup_key(service_date: Any, service_window: Any) -> tuple[str, str]:
    if isinstance(service_date, date):
        service_date_key = service_date.isoformat()
    else:
        service_date_key = str(service_date or "")
    return (service_date_key, str(service_window or ServiceWindow.DINNER.value))


def _coerce_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _weather_condition_code(payload: dict[str, Any]) -> str:
    weather_code = _coerce_int(payload.get("weather_code"))
    raw_condition = str(payload.get("conditions") or "").strip().lower()
    precip_chance = float(payload.get("precip_prob") or 0.0)
    precip_dinner_max = float(payload.get("precip_dinner_max") or 0.0)
    apparent_temp = _coerce_float(payload.get("apparent_temp_7pm"))
    temp_high = _coerce_float(payload.get("temperature_high"))
    temp_low = _coerce_float(payload.get("temperature_low"))
    wind_speed = _coerce_float(payload.get("wind_speed_mph"))
    cloud_cover_bin = _coerce_int(payload.get("cloudcover_bin"))

    if weather_code in {95, 96, 99} or "storm" in raw_condition:
        return "storm"
    if weather_code in {56, 57, 66, 67} or "freezing" in raw_condition or "sleet" in raw_condition:
        return "sleet"
    if weather_code in {71, 73, 75, 77, 85, 86} or "snow" in raw_condition:
        if weather_code in {75, 86} or precip_dinner_max >= 0.08 or precip_chance >= 0.75:
            return "snow_heavy"
        return "snow_light"
    if weather_code in {45, 48} or "fog" in raw_condition:
        return "fog"
    if precip_dinner_max > 0.0 or precip_chance >= 0.20 or "rain" in raw_condition:
        if weather_code in {65, 82} or precip_dinner_max >= 0.08 or precip_chance >= 0.75:
            return "rain_heavy"
        return "rain_light"
    if wind_speed is not None and wind_speed >= 22:
        return "wind_high"
    if (apparent_temp is not None and apparent_temp >= 88) or (temp_high is not None and temp_high >= 90):
        return "heat"
    if (apparent_temp is not None and apparent_temp <= 35) or (temp_low is not None and temp_low <= 35):
        return "cold"
    if cloud_cover_bin is not None:
        if cloud_cover_bin >= 3:
            return "overcast"
        if cloud_cover_bin == 2:
            return "cloudy"
        if cloud_cover_bin == 1:
            return "partly_cloudy"
        return "clear"
    if "partly" in raw_condition:
        return "partly_cloudy"
    if "overcast" in raw_condition:
        return "overcast"
    if "cloud" in raw_condition:
        return "cloudy"
    if raw_condition in {"clear", "sunny"}:
        return "clear"
    return "unknown"


def _build_card_weather_contract(
    *,
    raw_payload: dict[str, Any] | None,
    weather_effect_pct: Any,
) -> dict[str, Any] | None:
    payload = dict(raw_payload or {})
    if not payload and weather_effect_pct in {None, ""}:
        return None
    return {
        "conditionCode": _weather_condition_code(payload),
        "temperatureHigh": _coerce_float(payload.get("temperature_high")),
        "temperatureLow": _coerce_float(payload.get("temperature_low")),
        "temperatureUnit": "F",
        "apparentTemp7pm": _coerce_float(payload.get("apparent_temp_7pm")),
        "precipChance": _coerce_float(payload.get("precip_prob")),
        "precipDinnerMax": _coerce_float(payload.get("precip_dinner_max")),
        "windSpeedMph": _coerce_float(payload.get("wind_speed_mph")),
        "cloudCoverBin": _coerce_int(payload.get("cloudcover_bin")),
        "sunrise": str(payload.get("sunrise")) if payload.get("sunrise") else None,
        "sunset": str(payload.get("sunset")) if payload.get("sunset") else None,
        "weatherEffectPct": _coerce_float(weather_effect_pct),
    }


def _build_card_baseline_comparison_contract(card: dict[str, Any]) -> dict[str, Any] | None:
    baseline_covers = _coerce_int(card.get("baseline_total_covers"))
    delta_pct = _coerce_int(card.get("vs_usual_pct"))
    delta_covers = _coerce_int(card.get("vs_usual_covers"))
    if baseline_covers is None and delta_pct is None and delta_covers is None:
        return None
    return {
        "baselineCovers": baseline_covers,
        "deltaPct": delta_pct,
        "deltaCovers": delta_covers,
        "badgeText": forecast_vs_usual_text(delta_pct),
        "heroText": forecast_vs_usual_text(delta_pct, include_suffix=True),
    }


def _build_card_recent_change_contract(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    previous_expected = _coerce_int(snapshot.get("previous_expected"))
    current_expected = _coerce_int(snapshot.get("forecast_expected"))
    delta_covers = _coerce_int(snapshot.get("delta_expected"))
    snapshot_reason = str(snapshot.get("snapshot_reason") or "").strip() or None
    detail_text = forecast_recent_change_text(
        previous_expected=previous_expected,
        current_expected=current_expected,
        snapshot_reason=snapshot_reason,
        compact=False,
    )
    compact_text = forecast_recent_change_text(
        previous_expected=previous_expected,
        current_expected=current_expected,
        snapshot_reason=snapshot_reason,
        compact=True,
    )
    if detail_text is None and compact_text is None and delta_covers is None and snapshot_reason is None:
        return None
    return {
        "previousExpected": previous_expected,
        "currentExpected": current_expected,
        "deltaCovers": delta_covers,
        "snapshotReason": snapshot_reason,
        "text": translate_operator_text(detail_text or ""),
        "compactText": translate_operator_text(compact_text or ""),
    }


def _build_card_recent_change_map(recent_snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in recent_snapshots:
        if not snapshot.get("changed"):
            continue
        key = _card_lookup_key(snapshot.get("service_date"), snapshot.get("service_window"))
        contract = _build_card_recent_change_contract(snapshot)
        if contract is not None:
            result[key] = contract
    return result


def _load_card_weather_payloads(
    db: Database,
    *,
    operator_id: str,
    reference_date: date,
) -> dict[tuple[str, str], dict[str, Any]]:
    rows = db.fetchall(
        """
        WITH latest AS (
            SELECT forecast_for_date,
                   COALESCE(service_window, 'dinner') AS service_window,
                   weather_feature_blob,
                   ROW_NUMBER() OVER (
                       PARTITION BY forecast_for_date, COALESCE(service_window, 'dinner')
                       ORDER BY retrieved_at DESC, weather_pull_id DESC
                   ) AS rn
            FROM weather_pulls
            WHERE operator_id = ?
              AND forecast_for_date BETWEEN ? AND ?
        )
        SELECT forecast_for_date, service_window, weather_feature_blob
        FROM latest
        WHERE rn = 1
        ORDER BY forecast_for_date, service_window
        """,
        [operator_id, reference_date, reference_date.fromordinal(reference_date.toordinal() + ACTIONABLE_HORIZON_DAYS - 1)],
    )
    payloads: dict[tuple[str, str], dict[str, Any]] = {}
    for service_date, service_window, raw_blob in rows:
        if not raw_blob:
            continue
        try:
            parsed = json.loads(str(raw_blob))
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            payloads[_card_lookup_key(service_date, service_window)] = parsed
    return payloads


def _load_recorded_actual_dates(
    db: Database,
    *,
    operator_id: str,
    service_dates: list[date],
) -> set[str]:
    if not service_dates:
        return set()
    start_date = min(service_dates)
    end_date = max(service_dates)
    rows = db.fetchall(
        """
        SELECT service_date
        FROM operator_actuals
        WHERE operator_id = ?
          AND service_window = ?
          AND service_date BETWEEN ? AND ?
        """,
        [operator_id, ServiceWindow.DINNER.value, start_date, end_date],
    )
    return {
        row[0].isoformat() if isinstance(row[0], date) else str(row[0])
        for row in rows
        if row and row[0] is not None
    }


AUTHORITATIVE_SERVICE_STATE_SOURCES = {"operator", "connected_truth", "calendar_rule"}


def _normalized_service_state_name(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "normal":
        return ServiceState.NORMAL.value
    return normalized


def _forecast_service_state_needs_confirmation(
    *,
    card: dict[str, Any],
    digest: dict[str, Any],
    has_open_suggestion: bool,
) -> bool:
    if has_open_suggestion:
        return True
    service_state = _normalized_service_state_name(card.get("service_state"))
    if service_state in {"", ServiceState.NORMAL.value}:
        return False
    source = str(digest.get("service_state_source") or card.get("service_state_source") or "").strip().lower()
    reason = str(card.get("service_state_reason") or "").strip().lower()
    if source in AUTHORITATIVE_SERVICE_STATE_SOURCES:
        return False
    if reason in {"explicit operator input", "connected system truth", "calendar or holiday rule"}:
        return False
    return True


def _forecast_misses_planned_total(
    *,
    forecast_expected: Any,
    planned_total_covers: Any,
) -> bool:
    planned_total = _coerce_int(planned_total_covers)
    expected = _coerce_int(forecast_expected)
    if planned_total is None or expected is None:
        return False
    tolerance = max(2, int(round(max(1, planned_total) * 0.05)))
    return abs(expected - planned_total) > tolerance


def _build_card_status_map(
    *,
    db: Database,
    operator_id: str,
    reference_date: date,
    cards: list[dict[str, Any]],
    service_plan_window: dict[str, Any] | None,
    learning_agenda: list[dict[str, Any]],
    open_service_state_suggestions: list[dict[str, Any]],
    engine_digests: list[dict[str, Any]],
    operating_moment: str | None,
) -> dict[tuple[str, str], dict[str, str]]:
    if not cards:
        return {}

    service_dates: list[date] = []
    for card in cards:
        service_date = card.get("service_date")
        if isinstance(service_date, str):
            service_dates.append(date.fromisoformat(service_date))
        elif isinstance(service_date, date):
            service_dates.append(service_date)

    recorded_actual_dates = _load_recorded_actual_dates(
        db,
        operator_id=operator_id,
        service_dates=service_dates,
    )
    plan_entries = {
        _card_lookup_key(item.get("service_date"), ServiceWindow.DINNER.value): item
        for item in list((service_plan_window or {}).get("entries") or [])
    }
    agenda_items_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in learning_agenda:
        if str(item.get("status") or "open") != "open":
            continue
        if str(item.get("question_kind") or "") not in {"yes_no", "free_text"}:
            continue
        service_date = item.get("service_date")
        if service_date is None:
            continue
        agenda_items_by_key.setdefault(
            _card_lookup_key(service_date, ServiceWindow.DINNER.value),
            [],
        ).append(item)
    suggestion_keys = {
        _card_lookup_key(item.get("service_date"), item.get("service_window"))
        for item in open_service_state_suggestions
    }
    digest_by_key = {
        _card_lookup_key(item.get("service_date"), item.get("service_window")): item
        for item in engine_digests
    }

    now_naive = datetime.now(UTC).replace(tzinfo=None)
    statuses: dict[tuple[str, str], dict[str, str]] = {}
    for card in cards:
        key = _card_lookup_key(card.get("service_date"), card.get("service_window"))
        service_date_raw = card.get("service_date")
        service_date = date.fromisoformat(service_date_raw) if isinstance(service_date_raw, str) else service_date_raw
        day_offset = (service_date - reference_date).days if isinstance(service_date, date) else 999
        entry = plan_entries.get(key)
        digest = digest_by_key.get(key) or {}
        has_open_service_state_suggestion = key in suggestion_keys
        service_state_needs_confirmation = _forecast_service_state_needs_confirmation(
            card=card,
            digest=digest,
            has_open_suggestion=has_open_service_state_suggestion,
        )
        plan_status = "not_required"
        if entry is not None:
            reviewed = bool(entry.get("reviewed"))
            if not reviewed:
                plan_status = "pending"
            else:
                plan_updated_at = _coerce_datetime(entry.get("updated_at"))
                last_published_at = _coerce_datetime(card.get("last_published_at"))
                publication_after_plan = (
                    last_published_at is None
                    or plan_updated_at is None
                    or last_published_at > plan_updated_at
                )
                planned_service_state = _normalized_service_state_name(entry.get("service_state"))
                forecast_service_state = _normalized_service_state_name(card.get("service_state"))
                service_state_mismatch = (
                    planned_service_state != ""
                    and forecast_service_state not in {"", planned_service_state}
                )
                misses_planned_total = _forecast_misses_planned_total(
                    forecast_expected=card.get("forecast_expected"),
                    planned_total_covers=entry.get("planned_total_covers"),
                )
                if publication_after_plan and (
                    service_state_needs_confirmation
                    or service_state_mismatch
                    or misses_planned_total
                ):
                    plan_status = "stale"
                else:
                    plan_status = "submitted"

        learning_items = agenda_items_by_key.get(key, [])
        learning_status = "none"
        if learning_items:
            overdue = False
            for item in learning_items:
                last_asked_at = _coerce_datetime(item.get("last_asked_at"))
                if last_asked_at is not None and now_naive - last_asked_at >= timedelta(hours=24):
                    overdue = True
                    break
            learning_status = "overdue_question" if overdue else "open_question"

        service_date_key = service_date.isoformat() if isinstance(service_date, date) else str(service_date_raw or "")
        actuals_status = "not_due"
        if service_date_key in recorded_actual_dates:
            actuals_status = "recorded"
        elif service_date == reference_date:
            if operating_moment == "post_service_review":
                actuals_status = "due"
            elif operating_moment == "historical_review":
                actuals_status = "overdue"
        elif isinstance(service_date, date) and service_date < reference_date:
            actuals_status = "overdue"

        watch_status = "none"
        confidence_tier = str(card.get("confidence_tier") or "").lower()
        source_failure_count = int(digest.get("source_failure_count") or 0)
        connector_failure_count = int(digest.get("connector_failure_count") or 0)
        if service_state_needs_confirmation:
            watch_status = "service_state_risk"
        elif day_offset <= 4 and (source_failure_count > 0 or connector_failure_count > 0):
            watch_status = "material_uncertainty"
        elif day_offset <= 1 and confidence_tier in {"low", "very_low"}:
            watch_status = "low_confidence"
        elif day_offset <= 1 and list(card.get("major_uncertainties") or []):
            watch_status = "material_uncertainty"

        statuses[key] = {
            "planStatus": plan_status,
            "learningStatus": learning_status,
            "actualsStatus": actuals_status,
            "watchStatus": watch_status,
        }
    return statuses
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def load_setup_bootstrap_status(db: Database, operator_id: str | None) -> dict[str, Any] | None:
    if operator_id is None:
        return None
    row = db.fetchone(
        """
        SELECT operator_id, status, message, steps_json, started_at, updated_at, completed_at, failed_at, failure_reason
        FROM setup_bootstrap_runs
        WHERE operator_id = ?
        """,
        [operator_id],
    )
    if row is None:
        return None
    return {
        "operatorId": str(row[0]),
        "status": str(row[1]),
        "message": str(row[2] or ""),
        "steps": _parse_step_list(row[3]),
        "startedAt": serialize_value(row[4]),
        "updatedAt": serialize_value(row[5]),
        "completedAt": serialize_value(row[6]),
        "failedAt": serialize_value(row[7]),
        "failureReason": serialize_value(row[8]),
    }


def _persist_setup_bootstrap_status(
    db: Database,
    *,
    operator_id: str,
    status: str,
    message: str,
    steps: list[str],
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    failed_at: datetime | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    operator_exists = db.fetchone(
        "SELECT 1 FROM operators WHERE operator_id = ?",
        [operator_id],
    )
    if operator_exists is None:
        return {
            "operatorId": operator_id,
            "status": "cancelled",
            "message": message,
            "steps": list(steps),
            "startedAt": serialize_value(started_at),
            "updatedAt": serialize_value(_db_now()),
            "completedAt": serialize_value(completed_at),
            "failedAt": serialize_value(failed_at),
            "failureReason": serialize_value(failure_reason or "operator_deleted"),
        }

    updated_at = _db_now()
    db.execute(
        """
        INSERT INTO setup_bootstrap_runs (
            operator_id, status, message, steps_json, started_at, updated_at, completed_at, failed_at, failure_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(operator_id) DO UPDATE SET
            status = EXCLUDED.status,
            message = EXCLUDED.message,
            steps_json = EXCLUDED.steps_json,
            started_at = EXCLUDED.started_at,
            updated_at = EXCLUDED.updated_at,
            completed_at = EXCLUDED.completed_at,
            failed_at = EXCLUDED.failed_at,
            failure_reason = EXCLUDED.failure_reason
        """,
        [
            operator_id,
            status,
            message,
            json.dumps(steps),
            started_at,
            updated_at,
            completed_at,
            failed_at,
            failure_reason,
        ],
    )
    return load_setup_bootstrap_status(db, operator_id)


def _spawn_setup_bootstrap_thread(db_path: Path, operator_id: str) -> None:
    def worker() -> None:
        worker_db = Database(db_path=db_path)
        try:
            worker_db.initialize()
            _run_post_setup_bootstrap(worker_db, operator_id)
        finally:
            worker_db.close()
            with _SETUP_BOOTSTRAP_LOCK:
                active_thread = _SETUP_BOOTSTRAP_THREADS.get(operator_id)
                if active_thread is threading.current_thread():
                    _SETUP_BOOTSTRAP_THREADS.pop(operator_id, None)

    with _SETUP_BOOTSTRAP_LOCK:
        active_thread = _SETUP_BOOTSTRAP_THREADS.get(operator_id)
        if active_thread is not None and active_thread.is_alive():
            return
        thread = threading.Thread(
            target=worker,
            name=f"setup-bootstrap-{operator_id}",
            daemon=True,
        )
        _SETUP_BOOTSTRAP_THREADS[operator_id] = thread
        thread.start()


def ensure_setup_bootstrap_job(
    db: Database,
    operator_id: str,
    *,
    force_restart: bool = False,
) -> dict[str, Any]:
    existing = load_setup_bootstrap_status(db, operator_id)
    if existing and existing["status"] in ACTIVE_SETUP_BOOTSTRAP_STATUSES and not force_restart:
        _spawn_setup_bootstrap_thread(db.db_path, operator_id)
        return existing

    steps = ["Setting up your account.", "Pulling historical weather and preparing the first forecast refresh."]
    status = _persist_setup_bootstrap_status(
        db,
        operator_id=operator_id,
        status="pending",
        message="Setting up your account. Historical weather and the first refresh are running in the background.",
        steps=steps,
        started_at=_db_now(),
        completed_at=None,
        failed_at=None,
        failure_reason=None,
    )
    _spawn_setup_bootstrap_thread(db.db_path, operator_id)
    return status


def list_operator_summaries(db: Database) -> list[dict[str, Any]]:
    rows = db.fetchall(
        """
        SELECT operator_id, restaurant_name
        FROM operators
        WHERE operator_id NOT LIKE '_system%'
        ORDER BY operator_id
        """,
        [],
    )
    summaries: list[dict[str, Any]] = []
    for operator_id, restaurant_name in rows:
        context = _load_onboarding_context(db, str(operator_id))
        profile = context.get("profile")
        setup_bootstrap = load_setup_bootstrap_status(db, str(operator_id))
        published_count_row = db.fetchone(
            """
            SELECT COUNT(*)
            FROM published_forecast_state
            WHERE operator_id = ?
            """,
            [operator_id],
        )
        published_count = int(published_count_row[0] or 0) if published_count_row is not None else 0
        forecast_ready = bool(context.get("forecast_ready"))
        if setup_bootstrap and setup_bootstrap["status"] in ACTIVE_SETUP_BOOTSTRAP_STATUSES and published_count < ACTIONABLE_HORIZON_DAYS:
            forecast_ready = False
        summaries.append(
            {
                "operatorId": str(operator_id),
                "restaurantName": str(restaurant_name),
                "forecastReady": forecast_ready,
                "onboardingState": (
                    profile.onboarding_state.value if profile is not None else "missing"
                ),
            }
        )
    return summaries


def bootstrap_state(db: Database) -> dict[str, Any]:
    operators = list_operator_summaries(db)
    default_operator_id = operators[0]["operatorId"] if operators else None
    return {
        "operators": operators,
        "defaultOperatorId": default_operator_id,
        "operatorText": serialize_value(operator_text_contract()),
        "onboarding": {
            "options": onboarding_options(),
            "draft": empty_onboarding_draft(),
        },
    }


def build_workspace(
    db: Database,
    operator_id: str | None,
    *,
    reference_date: date | None = None,
    run_supervisor: bool = False,
) -> dict[str, Any]:
    resolved_reference_date = _resolve_reference_date(reference_date)
    operators = list_operator_summaries(db)
    if operator_id is None:
        return {
            "mode": "onboarding",
            "operators": operators,
            "operator": None,
            "setupBootstrap": None,
            "operatorText": serialize_value(operator_text_contract()),
            "onboarding": {
                "forecastReady": False,
                "summary": None,
                "draft": empty_onboarding_draft(),
                "options": onboarding_options(),
            },
            "dashboard": None,
            "chat": {
                "phase": "setup",
                "messages": [],
                "placeholder": "Tell me about your restaurant and how dinner service usually behaves...",
            },
        }

    repo = OperatorRepository(db)
    context = _load_onboarding_context(db, operator_id)
    profile = context.get("profile")
    location = context.get("location")
    if profile is None:
        return {
            "mode": "onboarding",
            "operators": operators,
            "operator": None,
            "setupBootstrap": None,
            "operatorText": serialize_value(operator_text_contract()),
            "onboarding": {
                "forecastReady": False,
                "summary": None,
                "draft": empty_onboarding_draft(),
                "options": onboarding_options(),
            },
            "dashboard": None,
            "chat": {
                "phase": "setup",
                "messages": [],
                "placeholder": "Tell me about your restaurant and how dinner service usually behaves...",
            },
        }

    setup_bootstrap = load_setup_bootstrap_status(db, operator_id)
    if setup_bootstrap and setup_bootstrap["status"] in ACTIVE_SETUP_BOOTSTRAP_STATUSES:
        _spawn_setup_bootstrap_thread(db.db_path, operator_id)

    if not context["forecast_ready"]:
        return {
            "mode": "onboarding",
            "operators": operators,
            "operator": _serialize_operator(profile),
            "setupBootstrap": setup_bootstrap,
            "operatorText": serialize_value(operator_text_contract()),
            "onboarding": {
                "forecastReady": False,
                "summary": serialize_value(context["summary"]),
                "draft": _build_onboarding_draft(profile, location, context["baselines"], context.get("upload_review")),
                "options": onboarding_options(),
            },
            "dashboard": None,
            "chat": {
                "phase": "setup",
                "messages": _serialize_messages(_load_recent_history(db, operator_id, limit=20)),
                "placeholder": "Tell me about your restaurant setup...",
            },
        }

    _sync_missing_actual_notifications(db, operator_id, resolved_reference_date)
    _sync_service_plan_notifications(db, operator_id, resolved_reference_date)
    packet = OperatorStatePacketService(db).build_packet(
        operator_id=operator_id,
        reference_date=resolved_reference_date,
        current_time=resolve_operator_current_time(
            timezone=profile.timezone,
            active_date=resolved_reference_date,
        ),
    )
    cards = list(packet.snapshot.actionable_cards)
    deltas = load_snapshot_deltas(db, operator_id)
    enrich_cards_with_deltas(cards, deltas)
    visible_cards = cards[:14]
    conversation = packet.conversation
    weather_payloads = _load_card_weather_payloads(
        db,
        operator_id=operator_id,
        reference_date=resolved_reference_date,
    )
    weather_effect_map = {
        _card_lookup_key(digest.get("service_date"), digest.get("service_window")): digest.get("weather_pct")
        for digest in list(conversation.engine_digests or [])
    }
    recent_change_map = _build_card_recent_change_map(conversation.recent_snapshots)
    missing_actuals = packet.conversation.missing_actuals
    service_plan_window = packet.conversation.service_plan_window
    card_status_map = _build_card_status_map(
        db=db,
        operator_id=operator_id,
        reference_date=resolved_reference_date,
        cards=visible_cards,
        service_plan_window=service_plan_window,
        learning_agenda=conversation.learning_agenda,
        open_service_state_suggestions=conversation.open_service_state_suggestions,
        engine_digests=conversation.engine_digests,
        operating_moment=str((conversation.operator_attention_summary or {}).get("operating_moment") or ""),
    )

    notifications = load_pending_notifications(db, operator_id)
    if notifications:
        notification_message = notifications_to_chat_message(
            notifications,
            conversation.operator_attention_summary,
        )
        notification_created_at = max(
            (
                _coerce_datetime(item.get("created_at"))
                for item in notifications
            ),
            default=None,
        ) or _db_now()
        if notification_message:
            _persist_notification_message(
                db,
                operator_id=operator_id,
                content=notification_message,
                created_at=notification_created_at,
            )
        mark_notifications_delivered(db, [item["notification_id"] for item in notifications])
    pending_notification_count_row = db.fetchone(
        "SELECT COUNT(*) FROM notification_events WHERE operator_id = ? AND status = 'pending'",
        [operator_id],
    )
    pending_notification_count = int(pending_notification_count_row[0] or 0) if pending_notification_count_row is not None else 0

    phase = detect_phase(db, operator_id)
    history = _load_recent_history(db, operator_id, limit=40)
    if not history:
        history = [
            {
                "message_id": None,
                "role": "assistant",
                "content": _build_morning_briefing(visible_cards, resolved_reference_date),
                "created_at": _db_now(),
            }
        ]

    return {
        "mode": "operations",
        "operators": operators,
        "operator": _serialize_operator(profile),
        "setupBootstrap": setup_bootstrap,
        "operatorText": serialize_value(operator_text_contract()),
        "onboarding": {
            "forecastReady": True,
            "summary": serialize_value(context["summary"]),
            "draft": _build_onboarding_draft(profile, location, context["baselines"], context.get("upload_review")),
            "options": onboarding_options(),
        },
        "dashboard": {
            "referenceDate": resolved_reference_date.isoformat(),
            "cards": [
                _serialize_card(
                    card,
                    weather_payload=weather_payloads.get(
                        _card_lookup_key(card.get("service_date"), card.get("service_window"))
                    ),
                    weather_effect_pct=weather_effect_map.get(
                        _card_lookup_key(card.get("service_date"), card.get("service_window"))
                    ),
                    recent_change=recent_change_map.get(
                        _card_lookup_key(card.get("service_date"), card.get("service_window"))
                    ),
                    status=card_status_map.get(
                        _card_lookup_key(card.get("service_date"), card.get("service_window"))
                    ),
                )
                for card in visible_cards
            ],
            "contextLine": _build_context_line(visible_cards, resolved_reference_date),
            "latestRefresh": serialize_value(packet.snapshot.latest_refresh),
            "pendingNotificationCount": pending_notification_count,
            "openServiceStateSuggestions": serialize_value(conversation.open_service_state_suggestions),
            "missingActuals": _serialize_missing_actuals(missing_actuals),
            "servicePlanWindow": _serialize_service_plan_window(service_plan_window),
            "operatorAttentionSummary": _serialize_attention_summary(conversation.operator_attention_summary),
            "learningAgenda": _serialize_learning_agenda(conversation.learning_agenda),
        },
        "chat": {
            "phase": phase,
            "messages": _serialize_messages(history),
            "placeholder": _chat_placeholder(phase, has_service_plan_due=bool(service_plan_window and int(service_plan_window.get("due_count") or 0) > 0)),
        },
    }


def complete_onboarding(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    draft = empty_onboarding_draft()
    draft.update(payload)

    restaurant_name = str(draft.get("restaurantName", "")).strip()
    canonical_address = str(draft.get("canonicalAddress", "")).strip()
    forecast_input_mode = str(draft.get("forecastInputMode") or FORECAST_INPUT_MODES[0]).strip() or FORECAST_INPUT_MODES[0]
    upload_token = str(draft.get("historicalUploadToken") or "").strip() or None
    baseline_source_type = "operator_setup"
    if forecast_input_mode not in FORECAST_INPUT_MODES:
        raise ValueError("The forecast input choice is not supported.")

    if forecast_input_mode == "historical_upload":
        if not upload_token:
            raise ValueError("Upload 12 months of history and pass the review before continuing.")
        review, _rows = load_staged_historical_upload(db, upload_token=upload_token)
        if not review.accepted:
            raise ValueError("The reviewed history upload is not ready yet.")
        baseline_values = {
            key: int(review.baseline_values.get(key) or 0)
            for key in ONBOARDING_BASELINE_KEYS
        }
        baseline_source_type = "historical_upload"
    else:
        baseline_values = {
            "mon_thu": int(draft.get("monThu", 0) or 0),
            "fri": int(draft.get("fri", 0) or 0),
            "sat": int(draft.get("sat", 0) or 0),
            "sun": int(draft.get("sun", 0) or 0),
        }
    has_complete_baselines = all(value > 0 for value in baseline_values.values())

    if not restaurant_name:
        raise ValueError("Restaurant name is required.")
    if not canonical_address:
        raise ValueError("Street address is required.")
    if not has_complete_baselines:
        if forecast_input_mode == "historical_upload":
            raise ValueError("The history upload did not yield all four day-group baselines yet.")
        raise ValueError("Enter all four dinner day-group cover counts to continue.")

    executor = ToolExecutor(
        db,
        provider=build_agent_model_provider(),
        defer_profile_enrichment=True,
    )
    profile_args: dict[str, Any] = {
        "restaurant_name": restaurant_name,
        "canonical_address": canonical_address,
        "city": str(draft.get("city", "")).strip() or None,
        "timezone": str(draft.get("timezone") or ONBOARDING_TIMEZONES[0]),
        "demand_mix": str(draft.get("demandMix") or DemandMix.MIXED.value),
        "neighborhood_type": str(draft.get("neighborhoodType") or NeighborhoodType.MIXED_URBAN.value),
        "patio_enabled": bool(draft.get("patioEnabled")),
        "weekly_baselines": baseline_values,
        "weekly_baseline_source_type": baseline_source_type,
    }
    if profile_args["patio_enabled"] and int(draft.get("patioSeatCapacity", 0) or 0) > 0:
        profile_args["patio_seat_capacity"] = int(draft.get("patioSeatCapacity", 0) or 0)
        profile_args["patio_season_mode"] = str(draft.get("patioSeasonMode") or PATIO_SEASON_MODES[0])

    operator_id = payload.get("operatorId")
    profile_result = executor.execute(operator_id, "update_profile", profile_args)
    if not profile_result.success:
        raise ValueError(profile_result.message or "Could not save the restaurant profile.")

    resolved_operator_id = str(profile_result.data.get("operator_id") or operator_id or "")
    if forecast_input_mode == "historical_upload" and upload_token:
        claim_historical_upload_for_operator(
            db,
            upload_token=upload_token,
            operator_id=resolved_operator_id,
        )
    else:
        clear_operator_reference_selection(db, resolved_operator_id)
    relevance_result = executor.execute(
        resolved_operator_id,
        "set_location_relevance",
        {
            "transit_relevance": bool(draft.get("transitRelevance")),
            "venue_relevance": bool(draft.get("venueRelevance")),
            "hotel_travel_relevance": bool(draft.get("hotelTravelRelevance")),
        },
    )
    readiness_result = executor.execute(resolved_operator_id, "check_readiness", {})
    ensure_setup_context_digests(
        db,
        operator_id=resolved_operator_id,
        reference_date=_resolve_reference_date(None),
        force=True,
    )
    bootstrap = None
    if readiness_result.success and readiness_result.data.get("forecast_ready"):
        bootstrap = ensure_setup_bootstrap_job(db, resolved_operator_id, force_restart=True)

    return {
        "operatorId": resolved_operator_id,
        "profile": serialize_value(profile_result.data),
        "locationContext": serialize_value(relevance_result.data if relevance_result.success else {}),
        "bootstrap": bootstrap,
        "workspace": build_workspace(db, resolved_operator_id, run_supervisor=False),
    }


def review_historical_upload(
    db: Database,
    *,
    file_name: str,
    content: str,
) -> dict[str, Any]:
    review = review_and_stage_historical_upload(
        db,
        file_name=file_name,
        content=content,
        provider=build_agent_model_provider(),
    )
    return serialize_value(asdict(review))


def post_chat_message(
    db: Database,
    operator_id: str,
    message: str,
    *,
    reference_date: date | None = None,
    learning_agenda_key: str | None = None,
    agent_dispatcher: "AgentDispatcher | None" = None,
) -> dict[str, Any]:
    resolved_reference_date = _resolve_reference_date(reference_date)
    provider = build_agent_model_provider()
    agent = UnifiedAgentService(db, provider=provider, agent_dispatcher=agent_dispatcher)
    response = agent.respond(
        operator_id=operator_id,
        message=message,
        reference_date=resolved_reference_date,
        learning_agenda_key=learning_agenda_key,
    )
    final_operator_id = response.operator_id or operator_id
    return {
        "operatorId": final_operator_id,
        "assistantMessage": response.text,
        "phase": response.phase,
        "suggestedMessages": [
            {
                "label": item.label,
                "value": item.value,
                "category": item.category,
            }
            for item in response.suggested_messages
        ],
        "workspace": build_workspace(db, final_operator_id, reference_date=resolved_reference_date, run_supervisor=False),
    }


def get_chat_history(
    db: Database,
    operator_id: str,
    *,
    before_id: int | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    page_limit = max(1, min(int(limit), 100))
    messages, has_more = _load_message_page(
        db,
        operator_id,
        before_id=before_id,
        limit=page_limit,
    )
    return {
        "messages": _serialize_messages(messages),
        "hasMore": has_more,
    }


def submit_actual_entry(
    db: Database,
    operator_id: str,
    payload: dict[str, Any],
    *,
    reference_date: date | None = None,
    agent_dispatcher: "AgentDispatcher | None" = None,
) -> dict[str, Any]:
    resolved_reference_date = _resolve_reference_date(reference_date)
    service_date_raw = payload.get("serviceDate")
    if not service_date_raw:
        raise ValueError("Service date is required.")
    try:
        service_date = service_date_raw if isinstance(service_date_raw, date) else date.fromisoformat(str(service_date_raw))
    except ValueError as exc:
        raise ValueError("Service date must be a valid ISO date.") from exc

    try:
        realized_total_covers = int(payload.get("realizedTotalCovers"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Total covers are required.") from exc
    if realized_total_covers < 0:
        raise ValueError("Total covers must be zero or greater.")

    realized_reserved_covers = _optional_int(payload.get("realizedReservedCovers"))
    realized_walk_in_covers = _optional_int(payload.get("realizedWalkInCovers"))
    outside_covers = _optional_int(payload.get("outsideCovers"))

    if realized_reserved_covers is not None and realized_reserved_covers < 0:
        raise ValueError("Reserved covers must be zero or greater.")
    if realized_walk_in_covers is not None and realized_walk_in_covers < 0:
        raise ValueError("Walk-in covers must be zero or greater.")
    if outside_covers is not None and outside_covers < 0:
        raise ValueError("Outside covers must be zero or greater.")
    if (
        realized_reserved_covers is not None
        and realized_walk_in_covers is not None
        and realized_reserved_covers + realized_walk_in_covers > realized_total_covers
    ):
        raise ValueError("Reserved plus walk-in covers cannot exceed total covers.")
    if outside_covers is not None and outside_covers > realized_total_covers:
        raise ValueError("Outside covers cannot exceed total covers.")

    service_state = _service_state_from_form_value(payload.get("serviceState"))
    note = str(payload.get("note") or "").strip() or None
    note_captured = False
    correction_staged = False

    result = record_actual_total_and_update(
        db,
        operator_id=operator_id,
        service_date=service_date,
        service_window=ServiceWindow.DINNER,
        realized_total_covers=realized_total_covers,
        realized_reserved_covers=realized_reserved_covers,
        realized_walk_in_covers=realized_walk_in_covers,
        outside_covers=outside_covers,
        service_state=service_state,
        entry_mode="manual_structured",
        note=note,
        agent_dispatcher=agent_dispatcher,
    )
    if note:
        from stormready_v3.conversation.notes import ConversationNoteService

        try:
            note_result = ConversationNoteService(db, agent_dispatcher=agent_dispatcher).record_note(
                operator_id=operator_id,
                note=note,
                service_date=service_date,
                service_window=ServiceWindow.DINNER,
            )
        except Exception:
            note_result = None
        else:
            note_captured = True
            correction_staged = note_result.correction_suggestion_id is not None
    _resolve_actual_due_notifications(db, operator_id=operator_id, service_date=service_date, service_window=ServiceWindow.DINNER)
    if agent_dispatcher is not None:
        from stormready_v3.workflows.retriever_hooks import run_retriever_hooks

        run_retriever_hooks(
            db=db,
            dispatcher=agent_dispatcher,
            operator_id=operator_id,
            reference_date=resolved_reference_date,
            kinds=("current_state", "temporal"),
        )
    summary = f"Logged {realized_total_covers} covers for {service_date.isoformat()}."
    if result.get("learned"):
        summary += " Learning streams updated."
    if note_captured:
        summary += " Operator context note captured."
    return {
        "result": {
            "success": True,
            "message": summary,
            "data": {
                "serviceDate": service_date.isoformat(),
                "realizedTotalCovers": realized_total_covers,
                "learned": bool(result.get("learned")),
                "evaluated": bool(result.get("evaluated")),
                "noteCaptured": note_captured,
                "correctionStaged": correction_staged,
            },
        },
        "workspace": build_workspace(db, operator_id, reference_date=resolved_reference_date, run_supervisor=False),
    }


def submit_service_plan(
    db: Database,
    operator_id: str,
    payload: dict[str, Any],
    *,
    reference_date: date | None = None,
    agent_dispatcher: "AgentDispatcher | None" = None,
) -> dict[str, Any]:
    resolved_reference_date = _resolve_reference_date(reference_date)
    service_date_raw = payload.get("serviceDate")
    if not service_date_raw:
        raise ValueError("Service date is required.")
    try:
        service_date = service_date_raw if isinstance(service_date_raw, date) else date.fromisoformat(str(service_date_raw))
    except ValueError as exc:
        raise ValueError("Service date must be a valid ISO date.") from exc
    if service_date < resolved_reference_date:
        raise ValueError("Week-ahead plans only apply to today or future dates.")

    service_state = _service_state_from_form_value(payload.get("serviceState"))
    planned_total_covers = _optional_int(payload.get("plannedTotalCovers"))
    estimated_reduction_pct_raw = payload.get("estimatedReductionPct")
    estimated_reduction_pct = None
    if estimated_reduction_pct_raw not in {None, ""}:
        try:
            estimated_reduction_pct = float(estimated_reduction_pct_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Estimated reduction must be a number.") from exc
        if estimated_reduction_pct < 0 or estimated_reduction_pct > 95:
            raise ValueError("Estimated reduction must be between 0 and 95 percent.")
    if planned_total_covers is not None and planned_total_covers < 0:
        raise ValueError("Planned total covers must be zero or greater.")
    if service_state is ServiceState.NORMAL and (planned_total_covers is not None or estimated_reduction_pct is not None):
        raise ValueError("Set an abnormal service state before adding a planned total or reduction.")
    if service_state is ServiceState.CLOSED:
        planned_total_covers = 0
        estimated_reduction_pct = None

    note = str(payload.get("note") or "").strip() or None
    review_window_start = payload.get("reviewWindowStart")
    review_window_end = payload.get("reviewWindowEnd")
    review_window_start = date.fromisoformat(str(review_window_start)) if review_window_start else None
    review_window_end = date.fromisoformat(str(review_window_end)) if review_window_end else None

    repo = OperatorRepository(db)
    repo.replace_service_plan(
        operator_id=operator_id,
        service_date=service_date,
        service_window=ServiceWindow.DINNER,
        planned_service_state=service_state.value,
        planned_total_covers=planned_total_covers,
        estimated_reduction_pct=estimated_reduction_pct,
        raw_note=note,
        confirmed_by_operator=True,
        entry_mode="manual_structured",
        review_window_start=review_window_start,
        review_window_end=review_window_end,
        updated_at=_db_now(),
    )
    repo.insert_service_state_log(
        operator_id=operator_id,
        service_date=service_date,
        service_window=ServiceWindow.DINNER,
        service_state=service_state.value,
        source_type="operator_manual",
        source_name="service_plan",
        confidence="high",
        operator_confirmed=True,
        note=note or "saved week-ahead service plan",
    )

    refresh_status = _run_operator_requested_refresh(
        db,
        operator_id=operator_id,
        run_date=resolved_reference_date,
        note=f"service_plan::{service_date.isoformat()}",
        agent_dispatcher=agent_dispatcher,
    )
    summary = f"Saved the operating plan for {service_date.isoformat()} as {service_state_label(service_state.value)}."
    if planned_total_covers is not None:
        summary += f" Planned around {planned_total_covers} covers."
    elif estimated_reduction_pct is not None:
        summary += f" Expected reduction set to {round(estimated_reduction_pct)}%."
    if refresh_status.get("ran_refresh"):
        summary += " Forecasts refreshed."
    elif refresh_status.get("reason") == "refresh_in_progress":
        summary += " A refresh is already running."

    return {
        "result": {
            "success": True,
            "message": summary,
            "data": {
                "serviceDate": service_date.isoformat(),
                "serviceState": service_state.value,
                "plannedTotalCovers": planned_total_covers,
                "estimatedReductionPct": estimated_reduction_pct,
                "ranRefresh": bool(refresh_status.get("ran_refresh")),
            },
        },
        "workspace": build_workspace(db, operator_id, reference_date=resolved_reference_date, run_supervisor=False),
    }


def _run_operator_requested_refresh(
    db: Database,
    *,
    operator_id: str,
    run_date: date,
    note: str,
    agent_dispatcher: "AgentDispatcher | None" = None,
) -> dict[str, Any]:
    from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator
    from stormready_v3.orchestration.supervisor import SupervisorService

    supervisor = SupervisorService(
        DeterministicOrchestrator(db, agent_dispatcher=agent_dispatcher)
    )
    supervisor.enqueue_operator_refresh_request(
        operator_id=operator_id,
        requested_for_date=run_date,
        note=note,
    )
    tick_result = supervisor.run_operator_tick(
        operator_id=operator_id,
        now=datetime.now(UTC),
        process_queue=True,
        process_scheduled=False,
        process_event_mode=False,
    )
    return {
        "ran_refresh": bool(tick_result.queued_requests_completed),
        "reason": "refresh_in_progress" if not tick_result.queued_requests_completed else "operator_requested",
    }


def request_refresh_now(
    db: Database,
    operator_id: str,
    reason: str | None = None,
    *,
    reference_date: date | None = None,
    agent_dispatcher: "AgentDispatcher | None" = None,
) -> dict[str, Any]:
    resolved_reference_date = _resolve_reference_date(reference_date)
    refresh_status = _run_operator_requested_refresh(
        db,
        operator_id=operator_id,
        run_date=resolved_reference_date,
        note=f"api_refresh::{reason or 'operator requested'}",
        agent_dispatcher=agent_dispatcher,
    )
    success = True
    if refresh_status.get("ran_refresh"):
        message = "Forecasts refreshed. The dashboard is now up to date."
    elif refresh_status.get("reason") == "refresh_in_progress":
        message = "A refresh is already running. StormReady will use that cycle instead of starting another one."
    else:
        message = "Forecasts are already current — no refresh was needed."
    return {
        "result": {
            "toolName": "request_refresh",
            "success": success,
            "message": message,
            "data": serialize_value(refresh_status),
        },
        "workspace": build_workspace(db, operator_id, reference_date=resolved_reference_date, run_supervisor=False),
    }


def start_setup_bootstrap_now(db: Database, operator_id: str, *, reference_date: date | None = None) -> dict[str, Any]:
    resolved_reference_date = _resolve_reference_date(reference_date)
    status = ensure_setup_bootstrap_job(db, operator_id, force_restart=True)
    return {
        "setupBootstrap": status,
        "workspace": build_workspace(db, operator_id, reference_date=resolved_reference_date, run_supervisor=False),
    }


def delete_operator_profile(db: Database, operator_id: str) -> None:
    db.execute(
        """
        DELETE FROM prediction_components
        WHERE prediction_run_id IN (
            SELECT prediction_run_id
            FROM prediction_runs
            WHERE operator_id = ?
        )
        """,
        [operator_id],
    )

    delete_order = [
        "refresh_request_queue",
        "weather_pulls",
        "external_signal_log",
        "service_state_log",
        "engine_digest",
        "prediction_evaluations",
        "operator_actuals",
        "operator_service_plan",
        "forecast_publication_snapshots",
        "notification_events",
        "correction_suggestions",
        "conversation_note_log",
        "conversation_messages",
        "connector_truth_log",
        "external_scan_run_log",
        "published_forecast_state",
        "working_forecast_state",
        "prediction_runs",
        "forecast_refresh_runs",
        "setup_bootstrap_runs",
        "operator_reference_assets",
        "historical_cover_uploads",
        "weather_baseline_profile",
        "weather_sensitivity_state",
        "weather_signature_state",
        "context_effect_state",
        "source_reliability_state",
        "confidence_calibration_state",
        "prediction_adaptation_state",
        "service_state_risk_state",
        "baseline_learning_state",
        "component_learning_state",
        "external_scan_learning_state",
        "operator_behavior_state",
        "operator_weekly_baselines",
        "location_context_profile",
        "operator_service_profile",
        "operator_locations",
        "external_source_catalog",
        "system_connections",
        "operators",
    ]
    for table_name in delete_order:
        db.execute(f"DELETE FROM {table_name} WHERE operator_id = ?", [operator_id])


def _load_onboarding_baselines(db: Database, operator_id: str | None) -> dict[str, int | None]:
    baselines: dict[str, int | None] = {key: None for key in ONBOARDING_BASELINE_KEYS}
    if operator_id is None:
        return baselines
    rows = db.fetchall(
        """
        SELECT day_group, baseline_total_covers
        FROM (
            SELECT day_group, baseline_total_covers,
                   ROW_NUMBER() OVER (
                       PARTITION BY day_group
                       ORDER BY effective_from DESC, baseline_id DESC
                   ) AS rn
            FROM operator_weekly_baselines
            WHERE operator_id = ? AND service_window = 'dinner'
        ) ranked
        WHERE rn = 1
        """,
        [operator_id],
    )
    for day_group, baseline_total_covers in rows:
        key = str(day_group)
        if key in baselines:
            baselines[key] = int(baseline_total_covers)
    return baselines


def _load_onboarding_context(db: Database, operator_id: str | None) -> dict[str, Any]:
    repo = OperatorRepository(db)
    profile = repo.load_operator_profile(operator_id) if operator_id else None
    location = repo.load_location_context(operator_id) if operator_id else None
    baselines = _load_onboarding_baselines(db, operator_id)
    upload_review = load_operator_historical_upload_review(db, operator_id=operator_id) if operator_id else None
    has_baselines = any((baselines[key] or 0) > 0 for key in ONBOARDING_BASELINE_KEYS)
    summary = summarize_setup_readiness(profile, primary_window_has_baseline=has_baselines) if profile else None
    forecast_ready = bool(summary and summary.forecast_ready and profile and profile.canonical_address)
    return {
        "profile": profile,
        "location": location,
        "baselines": baselines,
        "upload_review": upload_review,
        "summary": summary,
        "forecast_ready": forecast_ready,
    }


def _build_onboarding_draft(
    profile: Any,
    location: Any,
    baselines: dict[str, int | None],
    upload_review: Any = None,
) -> dict[str, Any]:
    draft = empty_onboarding_draft()
    if profile:
        draft.update(
            {
                "restaurantName": profile.restaurant_name,
                "canonicalAddress": profile.canonical_address or "",
                "city": profile.city or "",
                "timezone": profile.timezone or ONBOARDING_TIMEZONES[0],
                "demandMix": profile.demand_mix.value,
                "neighborhoodType": profile.neighborhood_type.value,
                "patioEnabled": bool(profile.patio_enabled),
                "patioSeatCapacity": int(profile.patio_seat_capacity or 0),
                "patioSeasonMode": profile.patio_season_mode or PATIO_SEASON_MODES[0],
            }
        )
    draft["monThu"] = int(baselines.get("mon_thu") or 0)
    draft["fri"] = int(baselines.get("fri") or 0)
    draft["sat"] = int(baselines.get("sat") or 0)
    draft["sun"] = int(baselines.get("sun") or 0)
    if upload_review is not None and getattr(profile, "setup_mode", None) == "historical_upload":
        draft["forecastInputMode"] = "historical_upload"
        draft["historicalUploadToken"] = getattr(upload_review, "upload_token", None)
        draft["historicalUploadReview"] = serialize_value(asdict(upload_review))
    if location:
        draft.update(
            {
                "transitRelevance": bool(location.transit_relevance),
                "venueRelevance": bool(location.venue_relevance),
                "hotelTravelRelevance": bool(location.hotel_travel_relevance),
            }
        )
    return draft


def _serialize_operator(profile: Any) -> dict[str, Any]:
    return {
        "operatorId": profile.operator_id,
        "restaurantName": profile.restaurant_name,
        "canonicalAddress": profile.canonical_address,
        "city": profile.city,
        "timezone": profile.timezone,
        "lat": profile.lat,
        "lon": profile.lon,
        "neighborhoodType": profile.neighborhood_type.value,
        "demandMix": profile.demand_mix.value,
        "patioEnabled": profile.patio_enabled,
        "patioSeatCapacity": profile.patio_seat_capacity,
        "patioSeasonMode": profile.patio_season_mode,
        "onboardingState": profile.onboarding_state.value,
    }


def _serialize_card(
    card: dict[str, Any],
    *,
    weather_payload: dict[str, Any] | None = None,
    weather_effect_pct: Any = None,
    recent_change: dict[str, Any] | None = None,
    status: dict[str, str] | None = None,
) -> dict[str, Any]:
    service_date = card["service_date"]
    if isinstance(service_date, str):
        service_date = date.fromisoformat(service_date)
    forecast_expected = int(card.get("forecast_expected") or 0)
    forecast_low = int(card.get("forecast_low") or 0)
    forecast_high = int(card.get("forecast_high") or 0)
    confidence_tier = str(card.get("confidence_tier") or "medium")
    plan_low, plan_high = planning_band(
        forecast_expected=forecast_expected,
        forecast_low=forecast_low,
        forecast_high=forecast_high,
        confidence_tier=confidence_tier,
    )
    posture = str(card.get("posture") or "normal")
    service_state = str(card.get("service_state") or "normal_service")
    return {
        "serviceDate": service_date.isoformat(),
        "dayLabel": service_date.strftime("%a"),
        "dateLabel": service_date.strftime("%b %d").replace(" 0", " "),
        "forecastExpected": forecast_expected,
        "forecastLow": forecast_low,
        "forecastHigh": forecast_high,
        "planningRangeLabel": f"{plan_low}-{plan_high}",
        "posture": posture.lower(),
        "serviceState": service_state,
        "headline": card.get("headline"),
        "summary": translate_operator_text(str(card.get("summary") or "")),
        "topDrivers": [
            {"id": str(driver_name), "label": driver_label(str(driver_name))}
            for driver_name in list(card.get("top_drivers") or [])
        ],
        "majorUncertainties": [
            translate_operator_text(str(item))
            for item in list(card.get("major_uncertainties") or [])
            if str(item).strip()
        ],
        "baselineComparison": serialize_value(_build_card_baseline_comparison_contract(card)),
        "recentChange": serialize_value(recent_change),
        "status": {
            "planStatus": str((status or {}).get("planStatus") or "not_required"),
            "learningStatus": str((status or {}).get("learningStatus") or "none"),
            "actualsStatus": str((status or {}).get("actualsStatus") or "not_due"),
            "watchStatus": str((status or {}).get("watchStatus") or "none"),
        },
        "weather": serialize_value(
            _build_card_weather_contract(
                raw_payload=weather_payload,
                weather_effect_pct=weather_effect_pct,
            )
        ),
    }


def _serialize_missing_actuals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for row in rows:
        serialized.append(
            {
                "serviceDate": serialize_value(row.get("service_date")),
                "serviceWindow": str(row.get("service_window") or "dinner"),
                "forecastExpected": int(row.get("forecast_expected") or 0),
            }
        )
    return serialized


def _serialize_service_plan_window(window: dict[str, Any] | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "promptDate": serialize_value(window.get("prompt_date")),
        "windowStart": serialize_value(window.get("window_start")),
        "windowEnd": serialize_value(window.get("window_end")),
        "windowLabel": str(window.get("window_label") or ""),
        "dueCount": int(window.get("due_count") or 0),
        "pendingDates": [serialize_value(item) for item in list(window.get("pending_dates") or [])],
        "entries": [
            {
                "serviceDate": serialize_value(item.get("service_date")),
                "serviceState": str(item.get("service_state") or ServiceState.NORMAL.value),
                "plannedTotalCovers": item.get("planned_total_covers"),
                "estimatedReductionPct": item.get("estimated_reduction_pct"),
                "note": str(item.get("note") or ""),
                "reviewed": bool(item.get("reviewed")),
                "updatedAt": serialize_value(item.get("updated_at")),
            }
            for item in list(window.get("entries") or [])
        ],
    }


def _serialize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "assistant"))
        content = str(message.get("content", ""))
        if role == "assistant":
            content = translate_operator_text(content)
        serialized.append(
            {
                "messageId": int(message.get("message_id")) if message.get("message_id") not in {None, ""} else None,
                "role": role,
                "content": content,
                "kind": str(message.get("kind", "message")),
                "createdAt": serialize_value(message.get("created_at")) or serialize_value(_db_now()),
            }
        )
    return serialized


def _serialize_communication_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "category": serialize_value(value.get("category")),
        "what_is_true_now": translate_operator_text(str(value.get("what_is_true_now") or "")) or None,
        "why_it_matters": translate_operator_text(str(value.get("why_it_matters") or "")) or None,
        "what_i_need_from_you": translate_operator_text(str(value.get("what_i_need_from_you") or "")) or None,
        "what_is_still_uncertain": translate_operator_text(str(value.get("what_is_still_uncertain") or "")) or None,
        "one_question": translate_operator_text(str(value.get("one_question") or "")) or None,
        "facts": serialize_value(value.get("facts")),
    }


def _serialize_attention_section(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    payload = value.get("communication_payload")
    if not isinstance(payload, dict):
        return None
    return {
        "communicationPayload": _serialize_communication_payload(payload),
        "service_date": serialize_value(value.get("service_date")),
        "service_window": serialize_value(value.get("service_window")),
    }


def _serialize_attention_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "moment_label": serialize_value(value.get("moment_label")),
        "primary_focus_key": serialize_value(value.get("primary_focus_key")),
        "ordered_section_keys": serialize_value(value.get("ordered_section_keys")),
        "latest_material_change": _serialize_attention_section(value.get("latest_material_change")),
        "current_operational_watchout": _serialize_attention_section(value.get("current_operational_watchout")),
        "pending_operator_action": _serialize_attention_section(value.get("pending_operator_action")),
        "current_uncertainty": _serialize_attention_section(value.get("current_uncertainty")),
        "best_next_question": _serialize_attention_section(value.get("best_next_question")),
    }


def _serialize_learning_agenda_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    payload = value.get("communication_payload")
    if not isinstance(payload, dict):
        return None
    return {
        "agenda_key": str(value.get("agenda_key") or ""),
        "status": str(value.get("status") or ""),
        "question_kind": str(value.get("question_kind") or ""),
        "service_date": serialize_value(value.get("service_date")),
        "communicationPayload": _serialize_communication_payload(payload),
    }


def _serialize_learning_agenda(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in items:
        next_item = _serialize_learning_agenda_item(item)
        if next_item is not None:
            serialized.append(next_item)
    return serialized


def _serialize_semantic_value(value: Any) -> Any:
    serialized = serialize_value(value)
    return _with_camelcase_communication_payload(serialized)


def _with_camelcase_communication_payload(value: Any) -> Any:
    if isinstance(value, dict):
        next_value = {
            str(key): _with_camelcase_communication_payload(item)
            for key, item in value.items()
        }
        payload = next_value.get("communication_payload")
        if "communicationPayload" not in next_value and isinstance(payload, dict):
            next_value["communicationPayload"] = payload
            next_value.pop("communication_payload", None)
            next_value.pop("summary", None)
            next_value.pop("question_text", None)
        return next_value
    if isinstance(value, list):
        return [_with_camelcase_communication_payload(item) for item in value]
    return value


def _build_context_line(cards: list[dict[str, Any]], today: date) -> str:
    for card in cards:
        service_date = card["service_date"]
        if isinstance(service_date, str):
            service_date = date.fromisoformat(service_date)
        if service_date != today:
            continue
        plan_low, plan_high = planning_band(
            forecast_expected=int(card.get("forecast_expected") or 0),
            forecast_low=int(card.get("forecast_low") or 0),
            forecast_high=int(card.get("forecast_high") or 0),
            confidence_tier=str(card.get("confidence_tier") or "medium"),
        )
        if str(card.get("service_state") or "").lower() == "closed":
            return "Tonight: Closed"
        return f"Tonight: plan around {plan_low}-{plan_high} covers."
    if cards:
        first = cards[0]
        service_date = first["service_date"]
        if isinstance(service_date, str):
            service_date = date.fromisoformat(service_date)
        plan_low, plan_high = planning_band(
            forecast_expected=int(first.get("forecast_expected") or 0),
            forecast_low=int(first.get("forecast_low") or 0),
            forecast_high=int(first.get("forecast_high") or 0),
            confidence_tier=str(first.get("confidence_tier") or "medium"),
        )
        return f"{service_date.strftime('%A')}: plan around {plan_low}-{plan_high} covers."
    return ""


def _build_morning_briefing(cards: list[dict[str, Any]], today: date) -> str:
    if not cards:
        return (
            "Good morning! Your forecasts haven't been generated yet. "
            "Run a refresh and I’ll populate the next 14 days."
        )

    tonight = None
    for card in cards:
        service_date = card.get("service_date")
        if isinstance(service_date, str):
            service_date = date.fromisoformat(service_date)
        if service_date == today:
            tonight = card
            break

    parts: list[str] = []
    if tonight:
        forecast_expected = tonight.get("forecast_expected", "?")
        forecast_low = int(tonight.get("forecast_low") or 0)
        forecast_high = int(tonight.get("forecast_high") or 0)
        plan_low, plan_high = planning_band(
            forecast_expected=int(forecast_expected or 0),
            forecast_low=forecast_low,
            forecast_high=forecast_high,
            confidence_tier=str(tonight.get("confidence_tier") or "medium"),
        )
        parts.append(
            f"Tonight: plan around {plan_low}-{plan_high} covers, centered near {forecast_expected}."
        )
        top_drivers = list(tonight.get("top_drivers") or [])
        if top_drivers:
            parts.append(f"Main factor: {driver_label(str(top_drivers[0]))}.")
    else:
        first = cards[0]
        service_date = first.get("service_date")
        if isinstance(service_date, str):
            service_date = date.fromisoformat(service_date)
        parts.append(
            f"Next up is {service_date.strftime('%A')} at about {first.get('forecast_expected', '?')} covers."
        )
    parts.append("Pick any night above for detail, or ask me anything.")
    return " ".join(parts)


def _chat_placeholder(phase: str, *, has_service_plan_due: bool = False) -> str:
    if phase != "operations":
        return "Ask about tonight, the week ahead, or what changed in service..."
    if has_service_plan_due:
        return "Review the next service plans, or ask about any night in the week ahead..."
    hour = datetime.now().hour
    if hour >= 21:
        return "Ask about tonight, or use the actuals panel to log service results..."
    if hour >= 15:
        return "Ask about tonight, or tell me what's changing..."
    return "Ask about the week ahead, or review any actuals due..."


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(value)


def _service_state_from_form_value(raw_value: Any) -> ServiceState:
    normalized = str(raw_value or ServiceState.NORMAL.value).strip()
    mapping = {
        ServiceState.NORMAL.value: ServiceState.NORMAL,
        ServiceState.PARTIAL.value: ServiceState.PARTIAL,
        ServiceState.PATIO_CONSTRAINED.value: ServiceState.PATIO_CONSTRAINED,
        ServiceState.PRIVATE_EVENT.value: ServiceState.PRIVATE_EVENT,
        ServiceState.HOLIDAY_MODIFIED.value: ServiceState.HOLIDAY_MODIFIED,
        ServiceState.WEATHER_DISRUPTION.value: ServiceState.WEATHER_DISRUPTION,
        ServiceState.CLOSED.value: ServiceState.CLOSED,
    }
    return mapping.get(normalized, ServiceState.NORMAL)


def _service_plan_review_window(reference_date: date) -> dict[str, Any] | None:
    return service_plan_review_window(reference_date)


def _load_service_plan_window(db: Database, operator_id: str, reference_date: date) -> dict[str, Any] | None:
    return load_service_plan_window(db, operator_id, reference_date)


def _load_missing_actuals(db: Database, operator_id: str, reference_date: date) -> list[dict[str, Any]]:
    return load_missing_actuals(db, operator_id, reference_date)


def _sync_missing_actual_notifications(db: Database, operator_id: str, reference_date: date) -> None:
    missing_actuals = _load_missing_actuals(db, operator_id, reference_date)
    target = missing_actuals[0] if missing_actuals else None
    active_rows = db.fetchall(
        """
        SELECT notification_id, service_date, service_window, status
        FROM notification_events
        WHERE operator_id = ?
          AND notification_type = 'actuals_due'
          AND status IN ('pending', 'delivered')
        ORDER BY created_at DESC
        """,
        [operator_id],
    )

    target_key = None
    if target is not None:
        target_key = (target["service_date"], target["service_window"])

    for notification_id, service_date, service_window, status in active_rows:
        if target_key is not None and (service_date, service_window) == target_key:
            continue
        if str(status) == "pending":
            db.execute(
                """
                UPDATE notification_events
                SET status = 'resolved', delivered_at = ?
                WHERE notification_id = ?
                """,
                [_db_now(), notification_id],
            )

    if target is None:
        return

    already_active = any((service_date, service_window) == target_key for _, service_date, service_window, _ in active_rows)
    if already_active:
        return

    payload = {
        "missing_count": len(missing_actuals),
        "forecast_expected": int(target.get("forecast_expected") or 0),
        "confidence_tier": str(target.get("confidence_tier") or "medium"),
    }
    db.execute(
        """
        INSERT INTO notification_events (
            operator_id, service_date, service_window, notification_type, publish_reason, payload_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            operator_id,
            target["service_date"],
            target["service_window"],
            "actuals_due",
            "missing_actuals",
            json.dumps(payload),
            "pending",
        ],
    )


def _sync_service_plan_notifications(db: Database, operator_id: str, reference_date: date) -> None:
    window = _load_service_plan_window(db, operator_id, reference_date)
    active_rows = db.fetchall(
        """
        SELECT notification_id, service_date, service_window, status
        FROM notification_events
        WHERE operator_id = ?
          AND notification_type = 'service_plan_due'
          AND status IN ('pending', 'delivered')
        ORDER BY created_at DESC
        """,
        [operator_id],
    )
    target_key = None
    if window is not None and int(window.get("due_count") or 0) > 0:
        pending_dates = list(window.get("pending_dates") or [])
        if pending_dates:
            target_key = (pending_dates[0], ServiceWindow.DINNER.value)

    for notification_id, service_date, service_window, status in active_rows:
        if target_key is not None and (service_date, service_window) == target_key:
            continue
        if str(status) == "pending":
            db.execute(
                """
                UPDATE notification_events
                SET status = 'resolved', delivered_at = ?
                WHERE notification_id = ?
                """,
                [_db_now(), notification_id],
            )

    if target_key is None or window is None:
        return

    already_active = any((service_date, service_window) == target_key for _, service_date, service_window, _ in active_rows)
    if already_active:
        return

    pending_dates = [serialize_value(item) for item in list(window.get("pending_dates") or [])]
    payload = {
        "window_label": str(window.get("window_label") or ""),
        "window_start": serialize_value(window.get("window_start")),
        "window_end": serialize_value(window.get("window_end")),
        "pending_count": int(window.get("due_count") or 0),
        "pending_dates": pending_dates,
    }
    db.execute(
        """
        INSERT INTO notification_events (
            operator_id, service_date, service_window, notification_type, publish_reason, payload_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            operator_id,
            target_key[0],
            target_key[1],
            "service_plan_due",
            "week_ahead_plan",
            json.dumps(payload),
            "pending",
        ],
    )


def _resolve_actual_due_notifications(
    db: Database,
    *,
    operator_id: str,
    service_date: date,
    service_window: ServiceWindow,
) -> None:
    db.execute(
        """
        UPDATE notification_events
        SET status = 'resolved', delivered_at = ?
        WHERE operator_id = ?
          AND service_date = ?
          AND service_window = ?
          AND notification_type = 'actuals_due'
          AND status IN ('pending', 'delivered')
        """,
        [_db_now(), operator_id, service_date, service_window.value],
    )


def _weather_baseline_ready(db: Database, operator_id: str, service_window: str = "dinner") -> bool:
    row = db.fetchone(
        """
        SELECT COUNT(*) FROM weather_baseline_profile
        WHERE operator_id = ? AND service_window = ?
        """,
        [operator_id, service_window],
    )
    return bool(row and int(row[0] or 0) >= 12)


def _run_post_setup_bootstrap(db: Database, operator_id: str) -> dict[str, Any]:
    profile = OperatorRepository(db).load_operator_profile(operator_id)
    if profile is None:
        return _persist_setup_bootstrap_status(
            db,
            operator_id=operator_id,
            status="failed",
            message="Setup could not continue because the restaurant profile was not found.",
            steps=[],
            started_at=_db_now(),
            failed_at=_db_now(),
            failure_reason="profile_not_found",
        )

    started_at = _db_now()
    steps: list[str] = []
    cache_root = RUNTIME_DATA_ROOT / "weather_archive"
    operator_ready = _weather_baseline_ready(db, operator_id)
    brooklyn_ready = _weather_baseline_ready(db, BROOKLYN_SYSTEM_OPERATOR_ID)

    def persist(status: str, message: str, *, failure_reason: str | None = None) -> dict[str, Any]:
        completed_at = _db_now() if status == "completed" else None
        failed_at = _db_now() if status == "failed" else None
        return _persist_setup_bootstrap_status(
            db,
            operator_id=operator_id,
            status=status,
            message=message,
            steps=list(steps),
            started_at=started_at,
            completed_at=completed_at,
            failed_at=failed_at,
            failure_reason=failure_reason,
        )

    persist(
        "running",
        "Setting up your account. Historical weather and the first refresh are in progress.",
    )

    steps.append("Enriching the location profile for nearby demand and access context.")
    persist("running", "Enriching the location profile for nearby demand and access context.")
    try:
        from stormready_v3.setup.service import SetupService

        setup_service = SetupService(
            OperatorRepository(db),
            db=db,
            ai_provider=build_agent_model_provider(),
        )
        enriched = setup_service.run_location_enrichment(profile)
        if enriched:
            steps.append("Location profile enrichment saved.")
            persist("running", "Location profile enrichment saved.")
        else:
            steps.append("Location profile enrichment is still pending.")
            persist("running", "Location profile enrichment is still pending.")
    except Exception:
        steps.append("Location profile enrichment is still pending.")
        persist("running", "Location profile enrichment is still pending.")

    if not operator_ready:
        if profile.lat is not None and profile.lon is not None:
            steps.append("Building your weather history.")
            persist("running", "Building your weather history.")
            try:
                ensure_operator_weather_baseline(
                    db,
                    operator_id,
                    lat=profile.lat,
                    lon=profile.lon,
                    timezone=profile.timezone or "America/New_York",
                    service_window="dinner",
                    cache_root=cache_root,
                )
                operator_ready = True
                steps.append("Weather history ready.")
                persist("running", "Weather history ready.")
            except Exception:
                steps.append("Weather history is still pending.")
                persist("running", "Weather history is still pending.")
        else:
            steps.append("Address setup is still incomplete, so weather history is pending.")
            persist("running", "Waiting on address setup before weather history can be built.")
    else:
        steps.append("Weather history already available.")
        persist("running", "Weather history already available.")

    if not brooklyn_ready:
        steps.append("Preparing supporting weather data.")
        persist("running", "Preparing supporting weather data.")
        try:
            ensure_brooklyn_baseline(db, cache_root=cache_root)
            brooklyn_ready = True
            steps.append("Supporting weather data ready.")
            persist("running", "Supporting weather data ready.")
        except Exception:
            steps.append("Supporting weather data is still pending.")
            persist("running", "Supporting weather data is still pending.")
    else:
        steps.append("Supporting weather data already available.")
        persist("running", "Supporting weather data already available.")

    if profile.lat is not None and profile.lon is not None and profile.setup_mode == "historical_upload":
        steps.append("Evaluating whether your uploaded history should replace the default weather reference.")
        persist("running", "Evaluating whether your uploaded history should replace the default weather reference.")
        try:
            reference_result = train_operator_history_reference_asset(
                db,
                operator_id=operator_id,
                runtime_root=RUNTIME_DATA_ROOT,
                cache_root=cache_root,
            )
            steps.append(reference_result.message)
            persist("running", reference_result.message)
        except Exception:
            steps.append("Brooklyn remains active while the local weather reference stays pending.")
            persist("running", "Brooklyn remains active while the local weather reference stays pending.")

    steps.append("Refreshing forecasts so the new setup is immediately usable.")
    persist("running", "Refreshing forecasts so the setup is immediately usable.")
    refresh_result = ToolExecutor(db).execute(operator_id, "request_refresh", {"reason": "setup completion"})
    if refresh_result.success:
        steps.append("Forecasts refreshed.")
    else:
        steps.append(refresh_result.message or "The first refresh did not complete.")

    if refresh_result.success and operator_ready and brooklyn_ready:
        message = "Setup finished. Your forecasts are ready."
        return persist("completed", message)
    elif refresh_result.success and operator_ready:
        message = "Setup finished. Forecasts are ready, and a little background setup is still finishing."
        return persist("completed", message)
    elif refresh_result.success:
        message = "Setup finished. Forecasts are ready, and some weather setup is still finishing."
        return persist("completed", message)
    else:
        message = refresh_result.message or "Setup finished, but the first forecast refresh did not complete."
        return persist("failed", message, failure_reason=str(refresh_result.message or "refresh_failed"))
