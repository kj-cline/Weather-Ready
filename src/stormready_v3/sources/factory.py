from __future__ import annotations

from stormready_v3.config.settings import SOURCE_MODE
from stormready_v3.sources.local_context_live import build_live_local_context_sources
from stormready_v3.sources.mock import (
    MockBroadProxySource,
    MockCuratedLocalSource,
    MockDetailedWeatherSource,
    MockNarrativeContextSource,
    MockTransitSource,
    MockWeatherSource,
)
from stormready_v3.sources.nws import NWSActiveAlertsSource
from stormready_v3.sources.open_meteo import OpenMeteoForecastSource
from stormready_v3.sources.registry import SourceRegistry


def build_source_registry(mode: str | None = None) -> SourceRegistry:
    mode = (mode or SOURCE_MODE).lower()
    registry = SourceRegistry()
    if mode == "live":
        registry.register(OpenMeteoForecastSource.with_default_client())
        registry.register(NWSActiveAlertsSource.with_default_client())
        for adapter in build_live_local_context_sources():
            registry.register(adapter)
        return registry
    if mode == "hybrid":
        registry.register(OpenMeteoForecastSource.with_default_client())
        registry.register(NWSActiveAlertsSource.with_default_client())
        for adapter in build_live_local_context_sources():
            registry.register(adapter)
        registry.register(MockTransitSource())
        registry.register(MockCuratedLocalSource())
        registry.register(MockBroadProxySource())
        registry.register(MockNarrativeContextSource())
        return registry
    if mode == "detailed_mock":
        registry.register(MockDetailedWeatherSource())
        registry.register(MockTransitSource())
        registry.register(MockCuratedLocalSource())
        registry.register(MockBroadProxySource())
        registry.register(MockNarrativeContextSource())
        return registry
    registry.register(MockWeatherSource())
    return registry
