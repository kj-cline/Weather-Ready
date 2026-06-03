from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class PredictionGovernorOutput:
    emphasized_driver_indices: list[int] = field(default_factory=list)
    clarification_needed: bool = False
    clarification_question: str | None = None
    uncertainty_notes: list[str] = field(default_factory=list)
    governance_path: str = "deterministic_base"  # "deterministic_base", "ai"


@dataclass(slots=True)
class PublishGovernorOutput:
    override_notify: bool | None = None
    additional_publish_reason: str | None = None


@dataclass(slots=True)
class ConversationCapture:
    note: str | None = None
    suggested_service_state: str | None = None
    suggested_correction: dict[str, str] = field(default_factory=dict)
    qualitative_themes: list[str] = field(default_factory=list)
    extracted_facts: dict[str, object] = field(default_factory=dict)
    hypothesis_hints: list[str] = field(default_factory=list)
    observations: list[dict[str, object]] = field(default_factory=list)
