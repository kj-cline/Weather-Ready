"""Role-specific agent framework for StormReady V3.

Every specialized AI agent implements the contract defined here and is invoked
through ``AgentDispatcher``. The dispatcher is the only sanctioned entry point:
it enforces provider availability and exception safety before the model is ever
called, and logs every run regardless of outcome.

This module intentionally keeps the surface small: one policy dataclass, one
context, one result, one base class, one dispatcher. If a second-tier abstraction
is needed later it should be added here, not layered on from outside.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol


class AgentRole(str, Enum):
    """Every role in the system. Values are stable identifiers used in logs,
    policy file names, and the ``agent_run_log`` table — do not change existing values."""

    SIGNAL_INTERPRETER = "signal_interpreter"
    ANOMALY_EXPLAINER = "anomaly_explainer"
    PREDICTION_GOVERNOR = "prediction_governor"
    CURRENT_STATE_RETRIEVER = "current_state_retriever"
    TEMPORAL_MEMORY_RETRIEVER = "temporal_memory_retriever"
    CONVERSATION_ORCHESTRATOR = "conversation_orchestrator"
    CONVERSATION_NOTE_EXTRACTOR = "conversation_note_extractor"


class AgentStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentPolicy:
    """Declarative contract loaded from the agent's markdown policy file.

    Every field is load-bearing for safety. Strength caps gate Tier 1 vs Tier 2
    classification in the signal interpreter. ``allowed_categories`` is the
    taxonomy guardrail the output parser checks against. ``forbidden_source_classes``
    is the hard exclusion list (e.g. weather forecasts stay off the signal
    interpreter's path).
    """

    role: AgentRole
    version: int
    description: str
    trigger: str
    allowed_writes: tuple[str, ...]
    forbidden_writes: tuple[str, ...]
    allowed_categories: tuple[str, ...]
    forbidden_source_classes: tuple[str, ...]
    max_outputs_per_run: int
    max_tokens: int
    tier1_max_strength_per_signal: float
    tier1_max_strength_total: float
    requires_confirmation_when: tuple[str, ...]
    system_prompt_body: str

    def __post_init__(self) -> None:
        if self.max_outputs_per_run <= 0:
            raise ValueError("max_outputs_per_run must be > 0")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        if not 0.0 < self.tier1_max_strength_per_signal <= 1.0:
            raise ValueError("tier1_max_strength_per_signal must be in (0, 1]")
        if not 0.0 < self.tier1_max_strength_total <= 1.0:
            raise ValueError("tier1_max_strength_total must be in (0, 1]")
        if self.tier1_max_strength_per_signal > self.tier1_max_strength_total:
            raise ValueError("per-signal cap cannot exceed total cap")


@dataclass(frozen=True)
class AgentContext:
    """Runtime input to an agent invocation.

    ``payload`` is role-specific; the dispatcher does not inspect it.
    Each concrete agent is responsible for validating its own payload shape.
    """

    role: AgentRole
    operator_id: str
    run_id: str
    triggered_at: datetime
    payload: Mapping[str, Any]


@dataclass
class AgentResult:
    role: AgentRole
    run_id: str
    status: AgentStatus
    outputs: list[dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    tokens_used: int = 0
    error: str | None = None
    blocked_reason: str | None = None


class AgentModelProviderProto(Protocol):
    """Subset of ``ai.contracts.AgentModelProvider`` the framework needs.

    Kept narrow so tests can stub it with a plain class.
    """

    def is_available(self) -> bool: ...

    def structured_json_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 800,
    ) -> dict[str, Any] | None: ...


class AgentRunLogger(Protocol):
    """Write contract for ``agent_run_log`` rows.

    The wiring layer supplies a concrete implementation backed by the repository.
    The framework itself must not import repositories — that would invert the
    dependency direction. Tests supply a recording stub.
    """

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
    ) -> None: ...


class BaseAgent:
    """Abstract base every concrete agent inherits.

    Subclasses set the class-level ``role`` and implement ``run``. The constructor
    validates that the supplied policy matches the declared role, so a mis-pointed
    policy file fails at boot, not at runtime.
    """

    role: AgentRole

    def __init__(
        self,
        policy: AgentPolicy,
        provider: AgentModelProviderProto,
    ) -> None:
        if policy.role != self.role:
            raise ValueError(
                f"policy role {policy.role!r} does not match agent role {self.role!r}"
            )
        self.policy = policy
        self.provider = provider

    def run(self, ctx: AgentContext) -> AgentResult:
        raise NotImplementedError


class AgentDispatcher:
    """The sole sanctioned entry point for invoking agents.

    Responsibilities:
    - Refuse to run if no agent is registered for the requested role.
    - Refuse to run if the provider reports itself unavailable.
    - Catch exceptions from ``agent.run`` and convert them to FAILED results.
      Agents never raise past this boundary.
    - Log every invocation to ``agent_run_log`` regardless of outcome.

    The dispatcher does NOT inspect payloads, does NOT retry, and does NOT
    enforce ``allowed_writes`` against the result — the write-site caller is
    responsible for honoring the policy when committing outputs.
    """

    def __init__(
        self,
        agents: Mapping[AgentRole, BaseAgent],
        run_logger: AgentRunLogger,
    ) -> None:
        self._agents = dict(agents)
        self._run_logger = run_logger

    def dispatch(self, ctx: AgentContext) -> AgentResult:
        agent = self._agents.get(ctx.role)
        if agent is None:
            result = AgentResult(
                role=ctx.role,
                run_id=ctx.run_id,
                status=AgentStatus.BLOCKED,
                blocked_reason=f"no agent registered for role {ctx.role.value}",
            )
            self._safe_log(ctx, result)
            return result

        if not agent.provider.is_available():
            result = AgentResult(
                role=ctx.role,
                run_id=ctx.run_id,
                status=AgentStatus.BLOCKED,
                blocked_reason="provider unavailable",
            )
            self._safe_log(ctx, result)
            return result

        try:
            result = agent.run(ctx)
        except Exception as exc:  # noqa: BLE001 — last-line defense: agents must never raise past the dispatcher
            result = AgentResult(
                role=ctx.role,
                run_id=ctx.run_id,
                status=AgentStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )

        self._safe_log(ctx, result)
        return result

    def _safe_log(self, ctx: AgentContext, result: AgentResult) -> None:
        # Logging must never break the caller; a failed log is still better than
        # a failed refresh. The adapter catches its own DB errors in production.
        try:
            self._run_logger.record_run(
                role=ctx.role.value,
                run_id=ctx.run_id,
                operator_id=ctx.operator_id,
                status=result.status.value,
                tokens_used=result.tokens_used,
                outputs_count=len(result.outputs),
                triggered_at=ctx.triggered_at,
                error=result.error,
                blocked_reason=result.blocked_reason,
            )
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "AgentRole",
    "AgentStatus",
    "AgentPolicy",
    "AgentContext",
    "AgentResult",
    "AgentModelProviderProto",
    "AgentRunLogger",
    "BaseAgent",
    "AgentDispatcher",
]
