from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from stormready_v3.domain.models import OperatorProfile
from stormready_v3.sources.contracts import SourcePayload
from stormready_v3.domain.enums import ServiceWindow


class SourceAdapter(Protocol):
    source_name: str
    source_class: str

    def fetch(
        self,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload: ...


@dataclass(slots=True)
class SourceRegistry:
    adapters: dict[str, SourceAdapter] = field(default_factory=dict)

    def register(self, adapter: SourceAdapter) -> None:
        self.adapters[adapter.source_name] = adapter

    def fetch(
        self,
        source_name: str,
        *,
        operator_id: str,
        at: datetime,
        profile: OperatorProfile | None = None,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> SourcePayload:
        adapter = self.adapters[source_name]
        return adapter.fetch(
            operator_id=operator_id,
            at=at,
            profile=profile,
            service_date=service_date,
            service_window=service_window,
        )

    def list_sources(self) -> list[str]:
        return sorted(self.adapters)
