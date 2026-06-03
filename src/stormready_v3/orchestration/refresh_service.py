from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stormready_v3.domain.enums import RefreshReason
from stormready_v3.domain.models import LocationContextProfile
from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator


@dataclass(slots=True)
class RefreshServiceResult:
    operators_processed: int = 0


class RefreshService:
    def __init__(self, orchestrator: DeterministicOrchestrator) -> None:
        self.orchestrator = orchestrator

    def run_for_all_active_operators(
        self,
        *,
        refresh_reason: RefreshReason,
        run_date: date | None = None,
        refresh_window: str | None = None,
        event_mode_active: bool = False,
    ) -> RefreshServiceResult:
        result = RefreshServiceResult()
        for operator_id in self.orchestrator.operators.list_active_operator_ids():
            profile = self.orchestrator.operators.load_operator_profile(operator_id)
            if profile is None:
                continue
            location_context = self.orchestrator.operators.load_location_context(operator_id) or LocationContextProfile(
                operator_id=operator_id,
                neighborhood_archetype=profile.neighborhood_type,
            )
            plan = self.orchestrator.run_refresh_cycle(
                profile=profile,
                location_context=location_context,
                refresh_reason=refresh_reason,
                run_date=run_date,
                refresh_window=refresh_window,
                event_mode_active=event_mode_active,
            )
            result.operators_processed += 1
        return result
