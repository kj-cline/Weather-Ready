from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from stormready_v3.config.settings import CONNECTOR_SNAPSHOT_ROOT
from stormready_v3.connectors.contracts import ConnectorTruthCandidate
from stormready_v3.domain.enums import ServiceWindow


def _normalize_service_window(value: Any) -> ServiceWindow | None:
    if isinstance(value, ServiceWindow):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        for window in ServiceWindow:
            if window.value == normalized:
                return window
    return None


def _normalize_service_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


class SnapshotConnectorBase:
    system_name: str
    system_type: str

    def __init__(self, snapshot_root: Path | None = None) -> None:
        self.snapshot_root = Path(snapshot_root or CONNECTOR_SNAPSHOT_ROOT)

    def snapshot_path(self, operator_id: str) -> Path:
        return self.snapshot_root / self.system_name / f"{operator_id}.json"

    def _load_snapshot(self, operator_id: str) -> dict[str, Any] | None:
        path = self.snapshot_path(operator_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _candidate_from_record(
        self,
        *,
        operator_id: str,
        extracted_at: datetime,
        service_date: date | None,
        service_window: ServiceWindow | None,
        record: dict[str, Any],
        snapshot_path: Path,
    ) -> ConnectorTruthCandidate:
        return ConnectorTruthCandidate(
            system_name=self.system_name,
            system_type=self.system_type,
            extracted_at=extracted_at,
            operator_id=operator_id,
            service_date=service_date,
            service_window=service_window,
            fields=record,
            field_quality={},
            provenance={"snapshot_path": str(snapshot_path)},
        )

    def _select_record(
        self,
        records: list[dict[str, Any]],
        *,
        service_date: date | None,
        service_window: ServiceWindow | None,
    ) -> dict[str, Any] | None:
        best_record: dict[str, Any] | None = None
        for record in records:
            record_date = _normalize_service_date(record.get("service_date"))
            record_window = _normalize_service_window(record.get("service_window"))
            if service_date is not None and record_date not in {None, service_date}:
                continue
            if service_window is not None and record_window not in {None, service_window}:
                continue
            best_record = record
            if record_date == service_date and record_window == service_window:
                break
        return best_record

    def _extract_records(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        records = snapshot.get("records")
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]
        return [snapshot]

    def fetch_truth(
        self,
        *,
        operator_id: str,
        at: datetime,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> ConnectorTruthCandidate | None:
        path = self.snapshot_path(operator_id)
        snapshot = self._load_snapshot(operator_id)
        if snapshot is None:
            return None
        record = self._select_record(
            self._extract_records(snapshot),
            service_date=service_date,
            service_window=service_window,
        )
        if record is None:
            return None
        return self._candidate_from_record(
            operator_id=operator_id,
            extracted_at=at,
            service_date=service_date,
            service_window=service_window,
            record=record,
            snapshot_path=path,
        )


class OpenTableSnapshotConnector(SnapshotConnectorBase):
    system_name = "opentable_snapshot"
    system_type = "reservation_connector"


class ToastSnapshotConnector(SnapshotConnectorBase):
    system_name = "toast_snapshot"
    system_type = "pos_connector"

