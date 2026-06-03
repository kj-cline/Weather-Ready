from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from stormready_v3.config.settings import (
    OPENTABLE_LOCATION_ID,
    OPENTABLE_PARTNER_TOKEN,
    TOAST_PARTNER_TOKEN,
    TOAST_RESTAURANT_GUID,
)
from stormready_v3.connectors.contracts import ConnectorTruthCandidate
from stormready_v3.domain.enums import ServiceWindow


@dataclass(slots=True)
class LiveConnectorConfigState:
    configured: bool
    missing_fields: list[str]
    blocking_reason: str | None


class LiveConnectorBase:
    system_name: str
    system_type: str
    runtime_mode = "live_partner"

    def config_state(self) -> LiveConnectorConfigState:
        raise NotImplementedError

    def is_configured(self) -> bool:
        return self.config_state().configured

    def fetch_truth(
        self,
        *,
        operator_id: str,
        at: datetime,
        service_date: date | None = None,
        service_window: ServiceWindow | None = None,
    ) -> ConnectorTruthCandidate | None:
        del operator_id, at, service_date, service_window
        if not self.is_configured():
            return None
        # Live vendor fetches are intentionally deferred until partner credentials
        # and tested endpoint contracts are available.
        return None

    def readiness_details(self) -> dict[str, Any]:
        state = self.config_state()
        return {
            "runtime_mode": self.runtime_mode,
            "configured": state.configured,
            "missing_fields": state.missing_fields,
            "blocking_reason": state.blocking_reason,
        }


class OpenTablePartnerConnector(LiveConnectorBase):
    system_name = "opentable_partner"
    system_type = "reservation_connector"

    def config_state(self) -> LiveConnectorConfigState:
        missing: list[str] = []
        if not OPENTABLE_PARTNER_TOKEN:
            missing.append("STORMREADY_V3_OPENTABLE_PARTNER_TOKEN")
        if not OPENTABLE_LOCATION_ID:
            missing.append("STORMREADY_V3_OPENTABLE_LOCATION_ID")
        return LiveConnectorConfigState(
            configured=not missing,
            missing_fields=missing,
            blocking_reason="missing partner credentials" if missing else "partner endpoint contract pending",
        )


class ToastPartnerConnector(LiveConnectorBase):
    system_name = "toast_partner"
    system_type = "pos_connector"

    def config_state(self) -> LiveConnectorConfigState:
        missing: list[str] = []
        if not TOAST_PARTNER_TOKEN:
            missing.append("STORMREADY_V3_TOAST_PARTNER_TOKEN")
        if not TOAST_RESTAURANT_GUID:
            missing.append("STORMREADY_V3_TOAST_RESTAURANT_GUID")
        return LiveConnectorConfigState(
            configured=not missing,
            missing_fields=missing,
            blocking_reason="missing partner credentials" if missing else "partner endpoint contract pending",
        )
