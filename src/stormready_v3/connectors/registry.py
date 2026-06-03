from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from stormready_v3.connectors.contracts import ConnectorTruthCandidate
from stormready_v3.domain.enums import ServiceWindow


class ConnectorAdapter(Protocol):
    system_name: str
    system_type: str

    def fetch_truth(
        self,
        *,
        operator_id: str,
        at: datetime,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> ConnectorTruthCandidate | None: ...


@dataclass(slots=True)
class ConnectorRegistry:
    adapters: dict[str, ConnectorAdapter] = field(default_factory=dict)

    def register(self, adapter: ConnectorAdapter) -> None:
        self.adapters[adapter.system_name] = adapter

    def fetch_truth(
        self,
        system_name: str,
        *,
        operator_id: str,
        at: datetime,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> ConnectorTruthCandidate | None:
        adapter = self.adapters[system_name]
        return adapter.fetch_truth(
            operator_id=operator_id,
            at=at,
            service_date=service_date,
            service_window=service_window,
        )

    def list_systems(self) -> list[str]:
        return sorted(self.adapters)
