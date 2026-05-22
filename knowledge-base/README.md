# StormReady V3 — Knowledge Base

Seven nodes. Start at `01_system_overview.md` for the end-to-end story and
both diagrams (C4 container + process loops). Each node lists its key files,
contracts, and edges to other nodes.

1. [01_system_overview.md](01_system_overview.md) — what the system does, C4 + process diagrams
2. [02_frontend.md](02_frontend.md) — React SPA
3. [03_api_layer.md](03_api_layer.md) — FastAPI app, lifespan, endpoints
4. [04_agents.md](04_agents.md) — 7-role dispatcher framework + 3 ad-hoc AI sites
5. [05_orchestration.md](05_orchestration.md) — refresh pipeline, supervisor, prediction, publish
6. [06_data_layer.md](06_data_layer.md) — DuckDB, migrations, repositories
7. [07_external_world.md](07_external_world.md) — weather, signals, LLM provider

## How to use this KB

- Read `01` first — it gives you the two diagrams and the end-to-end story.
- Jump to the node whose area your task touches. Each node is ≤ ~80 lines.
- Follow the `→ see:` line at the bottom of each node to cross to related nodes.
- References use `package/file.py::symbol` — open or grep directly, don't re-scan.

## Conventions

- File references are relative to `src/stormready_v3/` unless they start with `frontend/`, `db/`, or `tests/`.
- Database tables are in `code`.
- "Dispatch" always means `AgentDispatcher.dispatch(ctx) → AgentResult`.
- "Digest" always means a row in `operator_context_digest` (migration 029).
