# 06 — Data Layer

DuckDB, embedded. Single file at `~/.stormready_v3/local/stormready_v3.duckdb`
(override with `STORMREADY_V3_DB_PATH`). 29 sequential SQL migrations applied
on startup.

## Key files

- `storage/db.py::Database` (line 13) — wrapper + migration runner
- `storage/repositories.py` — all repositories
- `db/migrations/001_initial_schema.sql` … `db/migrations/029_operator_context_digest.sql`

## Load-bearing tables

**Identity & profile**
- `operators` — id, name, city, address, timezone
- `operator_behavior_state` — brevity, staffing-risk bias

**Forecasting**
- `published_forecast_state` — current dinner forecast strip
- `forecast_refresh_runs` — refresh audit trail
- `operator_service_plan` (020) — planned covers
- `operator_actuals` — realized covers
- `operator_weekly_baselines` — day-of-week baseline demand
- `prediction_adaptation_state` (018) — per-operator model adjustments

**Agent context** (migration 029)
- `operator_context_digest` — cached digests keyed by `operator_id + kind` (`current_state`, `temporal`, `setup`). Capped at 100 rows per key on insert.

**Conversation**
- `conversation_messages` (014) — full chat history
- `conversation_note_log` (007) — structured note captures
- `conversation_learning_memory` (021) — derived from notes

**Learning**
- `operator_fact_memory` (021) — learned contextual facts
- `learning_agenda` (025) — open questions shown in UI
- `learning_decision_log` (023) — audit of learning actions
- `operator_observation_log` (022) — free-text observations
- `external_scan_learning` (010) — source-catalog learning signals

**External signals**
- `external_source_catalog` (011) — discovered sources
- `external_source_governance` (012, 013) — governance provenance
- `external_signal_log` — raw signal history
- `weather_pulls` — weather fetch history
- `service_state_log` — service-state transitions

**Infra**
- `supervisor_runtime` (008) — supervisor tick log
- `agent_runs` (028) — agent dispatch audit
- `reference_assets` (027) — uploaded reference docs
- `setup_bootstrap_runs` (016) — onboarding run log

## Key repositories

- `storage/repositories.py::OperatorRepository` (line 19)
- `storage/repositories.py::OperatorContextDigestRepository` (line 996) — `insert_digest()` enforces the 100-row-per-key cap
- Conversation memory, fact memory, hypothesis, plan, and actuals repos all live in the same file

## Migrations

Applied in order by `Database.initialize()`. Highest is `029_operator_context_digest.sql`.
New migrations must take the next sequence number; never reorder.

→ see: 04 (agents — reads/writes digests), 05 (orchestration — refresh writes), 03 (api — how endpoints reach repos)
