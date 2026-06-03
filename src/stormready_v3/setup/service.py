from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from stormready_v3.ai.contracts import AgentModelProvider
from stormready_v3.config.settings import ENABLE_SETUP_WEATHER_BASELINE
from stormready_v3.domain.enums import DemandMix, NeighborhoodType, OnboardingState, ServiceWindow
from stormready_v3.domain.models import OperatorProfile
from stormready_v3.mvp_scope import MVP_PRIMARY_SERVICE_WINDOW, ensure_runtime_window_supported, runtime_service_windows
from stormready_v3.setup.geocoding import CensusGeocoder
from stormready_v3.setup.readiness import derive_onboarding_state
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository


@dataclass(slots=True)
class SetupRequest:
    operator_id: str
    restaurant_name: str
    canonical_address: str
    city: str | None = None
    timezone: str | None = None
    lat: float | None = None
    lon: float | None = None
    primary_service_window: ServiceWindow = ServiceWindow.DINNER
    active_service_windows: list[ServiceWindow] | None = None
    neighborhood_type: NeighborhoodType = NeighborhoodType.MIXED_URBAN
    demand_mix: DemandMix = DemandMix.MIXED
    indoor_seat_capacity: int | None = None
    patio_enabled: bool = False
    patio_seat_capacity: int | None = None
    patio_season_mode: str | None = None
    weekly_baselines: Mapping[str, int] | None = None
    weekly_baseline_source_type: str = "operator_setup"
    window_weekly_baselines: Mapping[str, Mapping[str, int]] | None = None


class SetupService:
    def __init__(
        self,
        operators: OperatorRepository,
        geocoder: CensusGeocoder | None = None,
        db: Database | None = None,
        ai_provider: AgentModelProvider | None = None,
    ) -> None:
        self.operators = operators
        self.geocoder = geocoder
        self.db = db
        self.ai_provider = ai_provider

    @staticmethod
    def _normalize_service_window(window_name: str) -> ServiceWindow:
        normalized = window_name.strip().lower().replace("-", "_").replace(" ", "_")
        for service_window in ServiceWindow:
            if service_window.value == normalized:
                return service_window
        raise ValueError(f"Unsupported service window baseline key: {window_name}")

    @staticmethod
    def _derive_setup_mode(
        *,
        primary_window_has_baseline: bool,
        weekly_baseline_source_type: str,
    ) -> str:
        if weekly_baseline_source_type == "historical_upload":
            return "historical_upload"
        if primary_window_has_baseline:
            return "baseline_first"
        return "incomplete"

    def _create_or_update_operator(self, request: SetupRequest, *, run_enrichment: bool) -> OperatorProfile:
        ensure_runtime_window_supported(request.primary_service_window, context="setup primary_service_window")
        geocode = None
        if (request.lat is None or request.lon is None) and request.canonical_address and self.geocoder is not None:
            try:
                geocode = self.geocoder.geocode(request.canonical_address)
            except Exception:
                geocode = None

        canonical_address = geocode.canonical_address if geocode is not None else request.canonical_address
        lat = geocode.lat if geocode is not None else request.lat
        lon = geocode.lon if geocode is not None else request.lon
        city = request.city or (geocode.city if geocode is not None else None)

        active_service_windows = runtime_service_windows(request.active_service_windows or [request.primary_service_window])
        primary_window_from_expanded = False
        if request.window_weekly_baselines:
            for service_window_name in request.window_weekly_baselines:
                normalized_window = self._normalize_service_window(service_window_name)
                if normalized_window != MVP_PRIMARY_SERVICE_WINDOW:
                    continue
                if normalized_window == request.primary_service_window:
                    primary_window_from_expanded = True
                    break
        has_meaningful_baselines = bool(
            request.weekly_baselines and any(v and v > 0 for v in request.weekly_baselines.values())
        )
        has_meaningful_window_baselines = bool(
            request.window_weekly_baselines
            and primary_window_from_expanded
            and any(
                v and v > 0
                for bl in request.window_weekly_baselines.values()
                for v in bl.values()
            )
        )
        primary_window_has_baseline = has_meaningful_baselines or has_meaningful_window_baselines
        setup_mode = self._derive_setup_mode(
            primary_window_has_baseline=primary_window_has_baseline,
            weekly_baseline_source_type=request.weekly_baseline_source_type,
        )
        onboarding_state = derive_onboarding_state(
            OperatorProfile(
                operator_id=request.operator_id,
                restaurant_name=request.restaurant_name,
                canonical_address=canonical_address,
                city=city,
                timezone=request.timezone,
                lat=lat,
                lon=lon,
                primary_service_window=MVP_PRIMARY_SERVICE_WINDOW,
                active_service_windows=active_service_windows,
                neighborhood_type=request.neighborhood_type,
                demand_mix=request.demand_mix,
                indoor_seat_capacity=request.indoor_seat_capacity,
                patio_enabled=request.patio_enabled,
                patio_seat_capacity=request.patio_seat_capacity,
                patio_season_mode=request.patio_season_mode,
                setup_mode=setup_mode,
            ),
            primary_window_has_baseline=primary_window_has_baseline,
        )
        profile = OperatorProfile(
            operator_id=request.operator_id,
            restaurant_name=request.restaurant_name,
            canonical_address=canonical_address,
            city=city,
            timezone=request.timezone,
            lat=lat,
            lon=lon,
            primary_service_window=MVP_PRIMARY_SERVICE_WINDOW,
            active_service_windows=active_service_windows,
            neighborhood_type=request.neighborhood_type,
            demand_mix=request.demand_mix,
            indoor_seat_capacity=request.indoor_seat_capacity,
            patio_enabled=request.patio_enabled,
            patio_seat_capacity=request.patio_seat_capacity,
            patio_season_mode=request.patio_season_mode,
            setup_mode=setup_mode,
            onboarding_state=onboarding_state,
        )
        self.operators.upsert_operator(profile)

        # AI-powered location profiling — enriches LocationContextProfile with real
        # relevance flags and seeds the external catalog with nearby entities.
        if run_enrichment:
            self._run_location_profiling(profile)

        if request.weekly_baselines:
            meaningful_baselines = {k: v for k, v in request.weekly_baselines.items() if v and v > 0}
            for day_group, baseline in meaningful_baselines.items():
                self.operators.upsert_weekly_baseline(
                    request.operator_id,
                    MVP_PRIMARY_SERVICE_WINDOW,
                    day_group,
                    baseline,
                    source_type=request.weekly_baseline_source_type,
                )
        if request.window_weekly_baselines:
            for service_window_name, baseline_map in request.window_weekly_baselines.items():
                service_window = self._normalize_service_window(service_window_name)
                if service_window != MVP_PRIMARY_SERVICE_WINDOW:
                    continue
                meaningful_window_baselines = {k: v for k, v in baseline_map.items() if v and v > 0}
                for day_group, baseline in meaningful_window_baselines.items():
                    self.operators.upsert_weekly_baseline(
                        request.operator_id,
                        service_window,
                        day_group,
                        baseline,
                    )
        # Weather-baseline archive fetch is optional enrichment. Keep setup responsive by
        # default and only do the multi-year archive pull when explicitly enabled.
        if ENABLE_SETUP_WEATHER_BASELINE and lat is not None and lon is not None and self.db is not None:
            self._fetch_weather_baseline(profile.operator_id, lat, lon, request.timezone)

        return profile

    def create_or_update_operator(
        self,
        request: SetupRequest,
        *,
        run_enrichment: bool = True,
    ) -> OperatorProfile:
        return self._create_or_update_operator(request, run_enrichment=run_enrichment)

    def run_location_enrichment(self, profile: OperatorProfile) -> bool:
        return self._run_location_profiling(profile)

    def _run_location_profiling(self, profile: OperatorProfile) -> bool:
        """Run AI-powered location profiling to enrich LocationContextProfile. Best-effort."""
        try:
            from stormready_v3.external_intelligence.location_profiler import LocationProfiler
            profiler = LocationProfiler(provider=self.ai_provider)
            output = profiler.profile_location(profile)
            profiler.apply_profile(output, self.operators)

            # Seed catalog with entity-based entries if we have DB access
            if output.catalog_seeds and self.db is not None:
                from stormready_v3.external_intelligence.catalog import ExternalSourceCatalogService
                catalog = ExternalSourceCatalogService(self.db)
                catalog._upsert_entries(output.catalog_seeds, scanned_at=datetime.now(UTC))
            return True
        except Exception:
            return False  # Location profiling is enrichment, not required for forecasting

    def _fetch_weather_baseline(self, operator_id: str, lat: float, lon: float, timezone: str | None) -> None:
        """Fetch multi-year weather archive and store monthly normals. Best-effort."""
        try:
            from stormready_v3.sources.weather_archive import ensure_operator_weather_baseline
            from stormready_v3.config.settings import RUNTIME_DATA_ROOT
            ensure_operator_weather_baseline(
                self.db,
                operator_id,
                lat=lat,
                lon=lon,
                timezone=timezone or "America/New_York",
                service_window="dinner",
                cache_root=RUNTIME_DATA_ROOT / "weather_archive",
            )
        except Exception:
            pass  # Weather baseline is enrichment, not required for forecasting
