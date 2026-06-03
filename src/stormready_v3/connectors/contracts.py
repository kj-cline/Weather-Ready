from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from stormready_v3.domain.enums import ServiceWindow


@dataclass(slots=True)
class ConnectorTruthCandidate:
    system_name: str
    system_type: str
    extracted_at: datetime
    operator_id: str | None = None
    service_date: date | None = None
    service_window: ServiceWindow | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    field_quality: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
