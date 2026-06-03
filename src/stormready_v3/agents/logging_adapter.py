"""Concrete AgentRunLogger backed by the repository.

Kept in the agents package so the framework module (base.py) stays free of
repository imports. The dispatcher swallows logging errors, but this adapter
still catches its own DB errors so a half-broken DB never escalates into a
failed forecast refresh.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..storage.db import Database
from ..storage.repositories import AgentFrameworkRepository

_LOG = logging.getLogger(__name__)


class AgentRunLoggerAdapter:
    def __init__(self, db: Database) -> None:
        self._repo = AgentFrameworkRepository(db)

    def record_run(
        self,
        *,
        role: str,
        run_id: str,
        operator_id: str,
        status: str,
        tokens_used: int,
        outputs_count: int,
        triggered_at: datetime,
        error: str | None,
        blocked_reason: str | None,
    ) -> None:
        try:
            self._repo.insert_agent_run_log(
                run_id=run_id,
                role=role,
                operator_id=operator_id,
                status=status,
                triggered_at=triggered_at,
                tokens_used=tokens_used,
                outputs_count=outputs_count,
                error=error,
                blocked_reason=blocked_reason,
            )
        except Exception:  # noqa: BLE001
            _LOG.warning("agent_run_log write failed for run_id=%s", run_id, exc_info=True)
