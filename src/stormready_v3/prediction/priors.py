from __future__ import annotations

from stormready_v3.domain.enums import DemandMix, ForecastRegime, HorizonMode, NeighborhoodType

DEMAND_MIX_SCALERS: dict[DemandMix, float] = {
    DemandMix.RESERVATION_LED: 0.60,
    DemandMix.MIXED: 1.00,
    DemandMix.WALK_IN_LED: 1.35,
}

RESERVATION_SHARE_PRIORS: dict[DemandMix, float] = {
    DemandMix.RESERVATION_LED: 0.60,
    DemandMix.MIXED: 0.40,
    DemandMix.WALK_IN_LED: 0.20,
}

RESERVATION_REALIZATION_PRIORS: dict[DemandMix, float] = {
    DemandMix.RESERVATION_LED: 0.95,
    DemandMix.MIXED: 0.92,
    DemandMix.WALK_IN_LED: 0.88,
}

INTERVAL_HALF_WIDTHS: dict[tuple[ForecastRegime, HorizonMode], float] = {
    (ForecastRegime.MINIMAL_COLD_START, HorizonMode.NEAR): 0.30,
    (ForecastRegime.MINIMAL_COLD_START, HorizonMode.MID): 0.38,
    (ForecastRegime.MINIMAL_COLD_START, HorizonMode.LONG): 0.50,
    (ForecastRegime.PROFILED_COLD_START, HorizonMode.NEAR): 0.22,
    (ForecastRegime.PROFILED_COLD_START, HorizonMode.MID): 0.30,
    (ForecastRegime.PROFILED_COLD_START, HorizonMode.LONG): 0.42,
    (ForecastRegime.FAST_TRACKED_COLD_START, HorizonMode.NEAR): 0.18,
    (ForecastRegime.FAST_TRACKED_COLD_START, HorizonMode.MID): 0.26,
    (ForecastRegime.FAST_TRACKED_COLD_START, HorizonMode.LONG): 0.38,
    (ForecastRegime.EARLY_LEARNING, HorizonMode.NEAR): 0.14,
    (ForecastRegime.EARLY_LEARNING, HorizonMode.MID): 0.20,
    (ForecastRegime.EARLY_LEARNING, HorizonMode.LONG): 0.30,
    (ForecastRegime.MATURE_LOCAL_MODEL, HorizonMode.NEAR): 0.10,
    (ForecastRegime.MATURE_LOCAL_MODEL, HorizonMode.MID): 0.16,
    (ForecastRegime.MATURE_LOCAL_MODEL, HorizonMode.LONG): 0.24,
}

# Thresholds for regime graduation
REGIME_GRADUATION_THRESHOLDS = {
    "early_learning_min_samples": 5,     # Minimum actuals to graduate from cold-start
    "mature_min_samples": 15,            # Minimum actuals to reach mature
    "mature_max_error_pct": 0.18,        # Mean abs % error must be below this
    "mature_min_coverage": 0.70,         # Interval coverage must be above this
}

CONTEXT_PCT_CAP = 0.08
LOCAL_WEATHER_PCT_CAP = 0.30

# Monthly seasonal adjustment by neighborhood type.
# Values are percentage shifts from annual average: +0.05 = 5% above normal.
# Based on typical DC metro restaurant patterns.
SEASONAL_PCT_BY_NEIGHBORHOOD: dict[NeighborhoodType, dict[int, float]] = {
    NeighborhoodType.OFFICE_HEAVY: {
        # Follows office calendar: dips in summer (vacation), Dec holidays, Jan
        1: -0.05, 2: -0.02, 3: 0.02, 4: 0.04, 5: 0.03,
        6: -0.03, 7: -0.08, 8: -0.06, 9: 0.03, 10: 0.05,
        11: 0.04, 12: 0.03,
    },
    NeighborhoodType.RESIDENTIAL: {
        # Steadier year-round, slight summer dip (vacation), holiday lift
        1: -0.03, 2: -0.01, 3: 0.01, 4: 0.02, 5: 0.02,
        6: -0.02, 7: -0.04, 8: -0.03, 9: 0.01, 10: 0.03,
        11: 0.02, 12: 0.02,
    },
    NeighborhoodType.MIXED_URBAN: {
        # Blend of office + residential
        1: -0.04, 2: -0.01, 3: 0.02, 4: 0.03, 5: 0.03,
        6: -0.02, 7: -0.06, 8: -0.04, 9: 0.02, 10: 0.04,
        11: 0.03, 12: 0.02,
    },
    NeighborhoodType.DESTINATION_NIGHTLIFE: {
        # Strong weekend/event driven, summer peak, winter dip
        1: -0.06, 2: -0.03, 3: 0.02, 4: 0.04, 5: 0.06,
        6: 0.05, 7: 0.03, 8: 0.02, 9: 0.01, 10: 0.04,
        11: -0.02, 12: -0.04,
    },
    NeighborhoodType.TRAVEL_HOTEL_STATION: {
        # Tourism-driven: cherry blossom (Mar-Apr), summer peak, convention fall
        1: -0.06, 2: -0.03, 3: 0.06, 4: 0.08, 5: 0.04,
        6: 0.03, 7: 0.02, 8: -0.02, 9: 0.03, 10: 0.05,
        11: -0.04, 12: -0.06,
    },
}


def seasonal_pct_for_date(service_date, neighborhood_type: NeighborhoodType) -> float:
    """Return the seasonal adjustment percentage for a given date and neighborhood."""
    from datetime import date as _date
    if isinstance(service_date, _date):
        month = service_date.month
    else:
        month = int(service_date)
    neighborhood_seasonals = SEASONAL_PCT_BY_NEIGHBORHOOD.get(neighborhood_type)
    if neighborhood_seasonals is None:
        return 0.0
    return neighborhood_seasonals.get(month, 0.0)
