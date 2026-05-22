# 03 — API Layer

FastAPI. Thin HTTP seam over the domain services. Heavy work lives in the
agent, orchestration, and storage packages.

## Key files

- `api/app.py` — app factory, lifespan, SPA mount
- `api/service.py` — HTTP → domain seam (chat, workspace, actuals, refresh, onboarding)
- `api/serializers.py` — Pydantic response models (frontend contract)
- `api/dependencies.py` — DI for Database, dispatcher, services

## Lifespan wiring

On startup (inside `api/app.py` lifespan):

1. `storage/db.py::Database.initialize()` — applies migrations 001 → 029
2. Builds `AgentModelProvider` (Azure OpenAI via `ai/openai_provider.py`)
3. `agents/factory.py::build_agent_dispatcher(...)` → stored on `app.state.agent_dispatcher`
4. Optionally starts `orchestration/supervisor.py::SupervisorService` in a daemon thread, gated on `background_supervisor_enabled()`

On shutdown: stop supervisor, close DB.

## Endpoint groups

- `GET  /api/health` — liveness
- `GET  /api/bootstrap` — initial workspace payload
- `POST /api/operators/{id}/chat` — operator message → dispatcher
- `GET  /api/operators/{id}/chat-history`
- `GET  /api/operators/{id}/workspace`
- `POST /api/onboarding/complete` — profile + setup digest seed
- `POST /api/onboarding/review-history-upload` — CSV pre-validation (uses ad-hoc AI)
- `POST /api/operators/{id}/actuals` — log covers, triggers temporal refresh hook
- `POST /api/operators/{id}/service-plan`
- `POST /api/operators/{id}/refresh` — force a refresh run
- `POST /api/operators/{id}/setup-bootstrap`
- `DELETE /api/operators/{id}`
- `GET /{path}` — SPA catch-all

## Chat turn flow

```
POST /api/operators/{id}/chat
  → api/service.py::post_chat_message
  → agents/unified.py::UnifiedAgentService.respond
      · phase detection (setup / enrichment / operations)
      · digest hydration from operator_context_digest
      · AgentDispatcher.dispatch(role=CONVERSATION_ORCHESTRATOR, ...)
      · agents/tools.py::ToolExecutor handles tool calls (capture_note, query)
      · follow-up turn if the agent requested one
  → serialized reply (text + suggested messages)
```

→ see: 04 (agents — dispatch internals), 05 (orchestration — what `/refresh` triggers), 06 (data — digest tables)
