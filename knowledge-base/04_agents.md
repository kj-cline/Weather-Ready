# 04 — Agents

Seven-role multi-agent framework built around a single `AgentDispatcher`.
All live AI in the chat and refresh paths flows through this framework.
Three ad-hoc AI call sites remain outside it, listed at the bottom.

## Core contracts

- `agents/base.py::AgentRole` (line 21) — enum of the seven roles
- `agents/base.py::AgentContext` (line 81) — dispatch input: role, operator_id, payload, digests
- `agents/base.py::AgentResult` (line 96) — dispatch output: text, tool calls, structured fields
- `agents/base.py::AgentDispatcher` (line 173) — `dispatch(ctx) → AgentResult`, routes by role
- `agents/factory.py::build_agent_dispatcher` (line 24) — constructs the dispatcher once at lifespan start
- `agents/policy_loader.py::load_policy` (line 31) — loads per-role system prompt + tool schema

## The seven roles

| Role | File | Purpose |
|---|---|---|
| `signal_interpreter` | `agents/signal_interpreter.py` | Classify external signals into risk deltas |
| `anomaly_explainer` | `agents/anomaly_explainer.py` | Plain-language driver explanations |
| `prediction_governor` | `agents/prediction_governor.py` | Validate / adjust forecast, emit governance fields |
| `current_state_retriever` | `agents/current_state_retriever.py` | Build the `current_state` digest |
| `temporal_memory_retriever` | `agents/temporal_memory_retriever.py` | Build the `temporal` digest from history + learning memory |
| `conversation_orchestrator` | `agents/conversation_orchestrator.py::ConversationOrchestratorAgent` (line 135) | Own natural-language replies with line-level grounding |
| `conversation_note_extractor` | `agents/conversation_note_extractor.py` | Structured note extraction from operator messages |

Each role has a policy file at `agents/policies/<role>.md` (system prompt,
persona, tool schema, output contract) loaded at factory build time.

## Chat dispatch

1. `agents/unified.py::UnifiedAgentService.respond` (line 528) detects phase and hydrates digests.
2. Builds an `AgentContext` with `role=CONVERSATION_ORCHESTRATOR`.
3. `dispatcher.dispatch(ctx)` returns text and tool calls.
4. `agents/tools.py::ToolExecutor` runs the tool calls (`capture_note`, query tools).
5. `capture_note` → `conversation/notes.py::ConversationNoteService` (line 75) → dispatches `conversation_note_extractor`.
6. Raw-note fallback: `agents/runtime.py::ConversationAgent` (only when no dispatcher is available; retained as a shim).

## Refresh dispatch

`orchestration/orchestrator.py::DeterministicOrchestrator` (line 79) dispatches, in order:

- `signal_interpreter` and `anomaly_explainer` on the signal pass
- `current_state_retriever` and `temporal_memory_retriever` to write digests
- `prediction_governor` to govern the candidate forecast; falls back to a deterministic `PredictionGovernorOutput` if the dispatcher is unavailable

## Remaining ad-hoc AI sites

Not yet under the dispatcher. Each guards on `provider.is_available()`.

1. `external_intelligence/location_profiler.py::LocationProfiler` (line 56) — onboarding; structured-JSON call that seeds the external source catalog from a new address.
2. `imports/history_upload.py::_ai_review_summary` (line 199) — history CSV review summary during onboarding upload validation.
3. `external_intelligence/catalog.py::ExternalSourceCatalogService._provider_governance` (line 342) — governance scoring for discovered external sources.

Decide site by site when migrating: leave ad-hoc, wrap under an existing role, or promote to a new role.

→ see: 03 (api — how chat enters dispatch), 05 (orchestration — who dispatches governor + retrievers), 06 (data — digest tables)
