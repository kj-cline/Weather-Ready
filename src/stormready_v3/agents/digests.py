"""Typed digest dataclasses produced by the two retriever agents.

The retrievers (`current_state_retriever`, `temporal_memory_retriever`) write
these digests to `operator_context_digest` as JSON on forecast/actual/note
events. The conversation orchestrator reads the latest row per kind and grounds
its replies line-by-line against these fields.

Both digests are frozen and JSON-serializable. Every list has a hard cap so the
prompt size is bounded regardless of operator activity. The `source_hash` field
is a content hash of the inputs the retriever used — the orchestrator compares
it to the live system hash to detect staleness.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


MAX_NEAR_HORIZON = 5
MAX_ACTIVE_SIGNALS = 5
MAX_DISCLAIMERS = 3
MAX_RECENT_MISSES = 3
MAX_ACTIVE_HYPOTHESES = 3
MAX_RECENT_PATTERNS = 3
MAX_OPERATOR_FACTS = 6
MAX_OPEN_QUESTIONS = 3


@dataclass(frozen=True)
class CurrentStateDigest:
    produced_at: datetime
    source_hash: str
    reference_date: date
    phase: str                               # setup | enrichment | operations
    identity: dict[str, Any]                 # operator_id, venue_name, location
    headline_forecast: dict[str, Any] | None
    near_horizon: list[dict[str, Any]]       # <= MAX_NEAR_HORIZON
    pending_action: dict[str, Any] | None
    current_uncertainty: str | None
    active_signals_summary: list[str]        # <= MAX_ACTIVE_SIGNALS
    missing_actuals: list[date]
    disclaimers: list[str]                   # <= MAX_DISCLAIMERS

    def __post_init__(self) -> None:
        if len(self.near_horizon) > MAX_NEAR_HORIZON:
            raise ValueError(f"near_horizon exceeds {MAX_NEAR_HORIZON}")
        if len(self.active_signals_summary) > MAX_ACTIVE_SIGNALS:
            raise ValueError(f"active_signals_summary exceeds {MAX_ACTIVE_SIGNALS}")
        if len(self.disclaimers) > MAX_DISCLAIMERS:
            raise ValueError(f"disclaimers exceeds {MAX_DISCLAIMERS}")
        if self.phase not in {"setup", "enrichment", "operations"}:
            raise ValueError(f"invalid phase {self.phase!r}")

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=_json_default, ensure_ascii=False)


@dataclass(frozen=True)
class TemporalContextDigest:
    produced_at: datetime
    source_hash: str
    conversation_state: str                  # cold_start | active | follow_up
    recent_misses: list[dict[str, Any]]      # <= MAX_RECENT_MISSES
    active_hypotheses: list[dict[str, Any]]  # <= MAX_ACTIVE_HYPOTHESES
    recent_patterns: list[str]               # <= MAX_RECENT_PATTERNS
    operator_facts: list[dict[str, Any]]     # <= MAX_OPERATOR_FACTS
    learning_maturity: dict[str, Any]
    open_questions: list[dict[str, Any]]     # <= MAX_OPEN_QUESTIONS
    disclaimers: list[str]                   # <= MAX_DISCLAIMERS

    def __post_init__(self) -> None:
        if len(self.recent_misses) > MAX_RECENT_MISSES:
            raise ValueError(f"recent_misses exceeds {MAX_RECENT_MISSES}")
        if len(self.active_hypotheses) > MAX_ACTIVE_HYPOTHESES:
            raise ValueError(f"active_hypotheses exceeds {MAX_ACTIVE_HYPOTHESES}")
        if len(self.recent_patterns) > MAX_RECENT_PATTERNS:
            raise ValueError(f"recent_patterns exceeds {MAX_RECENT_PATTERNS}")
        if len(self.operator_facts) > MAX_OPERATOR_FACTS:
            raise ValueError(f"operator_facts exceeds {MAX_OPERATOR_FACTS}")
        if len(self.open_questions) > MAX_OPEN_QUESTIONS:
            raise ValueError(f"open_questions exceeds {MAX_OPEN_QUESTIONS}")
        if len(self.disclaimers) > MAX_DISCLAIMERS:
            raise ValueError(f"disclaimers exceeds {MAX_DISCLAIMERS}")
        if self.conversation_state not in {"cold_start", "active", "follow_up"}:
            raise ValueError(f"invalid conversation_state {self.conversation_state!r}")

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=_json_default, ensure_ascii=False)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


__all__ = [
    "CurrentStateDigest",
    "TemporalContextDigest",
    "MAX_NEAR_HORIZON",
    "MAX_ACTIVE_SIGNALS",
    "MAX_DISCLAIMERS",
    "MAX_RECENT_MISSES",
    "MAX_ACTIVE_HYPOTHESES",
    "MAX_RECENT_PATTERNS",
    "MAX_OPERATOR_FACTS",
    "MAX_OPEN_QUESTIONS",
]
