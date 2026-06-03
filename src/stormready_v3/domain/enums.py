from __future__ import annotations

from enum import StrEnum


class ServiceWindow(StrEnum):
    LUNCH = "lunch"
    DINNER = "dinner"
    BRUNCH = "brunch"
    ALL_DAY = "all_day"
    CUSTOM = "custom_window"


class ServiceState(StrEnum):
    NORMAL = "normal_service"
    PARTIAL = "partial_service"
    PATIO_CONSTRAINED = "patio_closed_or_constrained"
    PRIVATE_EVENT = "private_event_or_buyout"
    HOLIDAY_MODIFIED = "holiday_modified_service"
    WEATHER_DISRUPTION = "weather_disruption_service"
    CLOSED = "closed"


class HorizonMode(StrEnum):
    NEAR = "near_0_3"
    MID = "mid_4_7"
    LONG = "long_8_14"


class ForecastRegime(StrEnum):
    MINIMAL_COLD_START = "minimal_cold_start"
    PROFILED_COLD_START = "profiled_cold_start"
    FAST_TRACKED_COLD_START = "fast_tracked_cold_start"
    EARLY_LEARNING = "early_learning"
    MATURE_LOCAL_MODEL = "mature_local_model"


class PredictionCase(StrEnum):
    BASIC_PROFILE = "basic_profile_no_history"
    IMPORTED_TOTAL_HISTORY = "imported_total_history"
    IMPORTED_HISTORY_WITH_RESERVATIONS = "imported_history_with_reservations"
    IMPORTED_DECOMPOSED_HISTORY = "imported_decomposed_history"
    POS_ONLY = "pos_connected_only"
    RESERVATION_ONLY = "reservation_connected_only"
    POS_AND_RESERVATION = "pos_and_reservation_connected"
    RICH_MANUAL = "rich_manual_logging"
    AMBIGUOUS = "ambiguous_data_state"


class DemandMix(StrEnum):
    RESERVATION_LED = "reservation_led"
    MIXED = "mixed"
    WALK_IN_LED = "walk_in_led"


class NeighborhoodType(StrEnum):
    OFFICE_HEAVY = "office_heavy"
    RESIDENTIAL = "residential"
    MIXED_URBAN = "mixed_urban"
    DESTINATION_NIGHTLIFE = "destination_nightlife"
    TRAVEL_HOTEL_STATION = "travel_hotel_station"


class OnboardingState(StrEnum):
    COLD_START_READY = "cold_start_ready"
    CONNECTIONS_PENDING = "cold_start_ready_with_connections_pending"
    PARTIAL = "setup_partial_but_usable"
    INCOMPLETE = "setup_incomplete"


class SignalRole(StrEnum):
    NUMERIC_MOVER = "numeric_mover"
    CONFIDENCE_MOVER = "confidence_mover"
    POSTURE_MOVER = "posture_mover"
    SERVICE_STATE_MODIFIER = "service_state_modifier"


class RefreshReason(StrEnum):
    SCHEDULED = "scheduled"
    OPERATOR_REQUESTED = "operator_requested"
    EVENT_MODE = "event_mode"


class StateDestination(StrEnum):
    PUBLISHED = "published_forecast_state"
    WORKING = "working_forecast_state"


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONFIGURED = "configured"
    ACTIVE = "active"
    ERROR = "error"


class ComponentState(StrEnum):
    UNSUPPORTED = "unsupported"
    PROVISIONAL = "provisional"
    OBSERVABLE = "observable"
    LEARNED = "learned"


class PublishDecision(StrEnum):
    PUBLISH = "publish"
    SUPPRESS = "suppress"
    PUBLISH_AND_NOTIFY = "publish_and_notify"
