from __future__ import annotations

from pathlib import Path

from stormready_v3.config.settings import CONNECTOR_MODE
from stormready_v3.connectors.live import OpenTablePartnerConnector, ToastPartnerConnector
from stormready_v3.connectors.registry import ConnectorRegistry
from stormready_v3.connectors.snapshot import OpenTableSnapshotConnector, ToastSnapshotConnector


def build_connector_registry(
    mode: str | None = None,
    *,
    snapshot_root: Path | None = None,
) -> ConnectorRegistry:
    mode = (mode or CONNECTOR_MODE).lower()
    registry = ConnectorRegistry()
    if mode in {"snapshot", "default", "hybrid"}:
        registry.register(OpenTableSnapshotConnector(snapshot_root=snapshot_root))
        registry.register(ToastSnapshotConnector(snapshot_root=snapshot_root))
    if mode in {"live", "hybrid"}:
        registry.register(OpenTablePartnerConnector())
        registry.register(ToastPartnerConnector())
    return registry
