from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(slots=True)
class SourcePayload:
    source_name: str
    source_class: str
    retrieved_at: datetime
    payload: dict[str, Any]
    freshness: str
    service_date: date | None = None
    service_window: str | None = None
    source_bucket: str = "weather_core"
    scan_scope: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
