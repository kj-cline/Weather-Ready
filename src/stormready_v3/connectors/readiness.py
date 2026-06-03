from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from stormready_v3.connectors.registry import ConnectorRegistry
from stormready_v3.storage.db import Database


@dataclass(slots=True)
class ConnectorReadiness:
    system_name: str
    system_type: str | None
    runtime_mode: str
    connection_state: str
    sync_mode: str | None
    truth_priority_rank: int | None
    registered_in_runtime: bool
    configured: bool
    ready_for_fetch: bool
    last_successful_sync_at: Any | None
    blocked_reason: str | None
    details: dict[str, Any]


class ConnectorReadinessService:
    def __init__(self, db: Database, registry: ConnectorRegistry) -> None:
        self.db = db
        self.registry = registry

    def build(self, operator_id: str) -> list[ConnectorReadiness]:
        rows = self.db.fetchall(
            """
            SELECT system_name, system_type, connection_state, sync_mode, last_successful_sync_at, truth_priority_rank
            FROM system_connections
            WHERE operator_id = ?
            ORDER BY COALESCE(truth_priority_rank, 999), system_name
            """,
            [operator_id],
        )
        connection_map = {
            str(row[0]): {
                "system_type": str(row[1]) if row[1] is not None else None,
                "connection_state": str(row[2]),
                "sync_mode": str(row[3]) if row[3] is not None else None,
                "last_successful_sync_at": row[4],
                "truth_priority_rank": int(row[5]) if row[5] is not None else None,
            }
            for row in rows
        }

        system_names = set(connection_map) | set(self.registry.adapters)
        readiness: list[ConnectorReadiness] = []
        for system_name in sorted(system_names):
            adapter = self.registry.adapters.get(system_name)
            connection = connection_map.get(system_name, {})
            runtime_mode = getattr(adapter, "runtime_mode", "snapshot" if "snapshot" in system_name else "unregistered")
            configured = True
            blocked_reason = None
            details: dict[str, Any] = {}

            if adapter is None:
                configured = False
                blocked_reason = "not registered in current connector mode"
            else:
                if hasattr(adapter, "is_configured"):
                    configured = bool(adapter.is_configured())
                if hasattr(adapter, "readiness_details"):
                    details = dict(getattr(adapter, "readiness_details")())
                    blocked_reason = details.get("blocking_reason")
                snapshot_path_fn = getattr(adapter, "snapshot_path", None)
                if callable(snapshot_path_fn):
                    snapshot_path = Path(snapshot_path_fn(operator_id))
                    details["snapshot_path"] = str(snapshot_path)
                    details["snapshot_exists"] = snapshot_path.exists()
                    if connection.get("connection_state") == "active" and not snapshot_path.exists():
                        blocked_reason = blocked_reason or "snapshot file missing"
                        configured = False

            connection_state = connection.get("connection_state", "not_connected")
            ready_for_fetch = bool(
                adapter is not None
                and configured
                and connection_state == "active"
                and blocked_reason in {None, "partner endpoint contract pending"}
            )
            if blocked_reason == "partner endpoint contract pending":
                ready_for_fetch = False

            readiness.append(
                ConnectorReadiness(
                    system_name=system_name,
                    system_type=connection.get("system_type") or getattr(adapter, "system_type", None),
                    runtime_mode=runtime_mode,
                    connection_state=connection_state,
                    sync_mode=connection.get("sync_mode"),
                    truth_priority_rank=connection.get("truth_priority_rank"),
                    registered_in_runtime=adapter is not None,
                    configured=configured,
                    ready_for_fetch=ready_for_fetch,
                    last_successful_sync_at=connection.get("last_successful_sync_at"),
                    blocked_reason=blocked_reason,
                    details=details,
                )
            )
        return readiness

    def build_rows(self, operator_id: str) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.build(operator_id)]
