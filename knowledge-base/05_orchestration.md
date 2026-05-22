# 05 — Orchestration

Everything that runs outside a direct HTTP request: the refresh pipeline,
the supervisor tick loop, retriever hooks, the prediction engine, and
publishing.

## Key files

- `orchestration/orchestrator.py::DeterministicOrchestrator` (line 79) — `refresh_operator(operator_id, reason)` pipeline
- `orchestration/supervisor.py::SupervisorService` (line 39) — background tick loop
- `orchestration/refresh_service.py` — queue + trigger API for refreshes
- `orchestration/planner.py` — scheduled window computation
- `workflows/retriever_hooks.py` — best-effort hooks after actuals and note capture
- `prediction/engine.py` — deterministic cover prediction
- `prediction/components/*.py` — day groups, baselines, weather deltas, signal deltas
- `publish/*.py` — forecast-strip publishing and notification dispatch

## Refresh pipeline

`DeterministicOrchestrator.refresh_operator` runs on `/refresh` or a supervisor tick:

1. Gather operator profile + history.
2. Pull weather (`sources/open_meteo.py`, `sources/nws.py`) and signals.
3. `signal_interpreter` dispatch — classify anomalies.
4. `anomaly_explainer` dispatch — driver text.
5. `prediction/engine.py` — candidate cover forecast.
6. `prediction_governor` dispatch — governance fields and any adjustment. Deterministic fallback if no dispatcher.
7. `current_state_retriever` + `temporal_memory_retriever` dispatches — write digest rows.
8. `publish/*` — store `published_forecast_state`, notify.
9. Log to `forecast_refresh_runs`.

## Supervisor loop

Runs in a daemon thread when `background_supervisor_enabled()` is true.

- Tick interval: `background_supervisor_interval_seconds()` (default ~60s)
- Each tick: drains the pending refresh queue, checks scheduled windows (`SCHEDULED_REFRESH_WINDOWS = ("morning", "midday", "pre_dinner")`), requests refreshes via `refresh_service`.
- Logs to `supervisor_runtime` (migration 008).

## Retriever hooks

`workflows/retriever_hooks.py` exposes best-effort entry points:

- After actuals submission → trigger `temporal_memory_retriever` digest refresh.
- After note capture → same.
- Swallows exceptions; never blocks the HTTP response.

## Prediction + learning feedback

`prediction/engine.py` reads from:

- `operator_weekly_baselines`
- `operator_fact_memory` (migration 021)
- `learning/hypothesis_state.py`
- `prediction_adaptation_state` (migration 018)

Learning decisions are audited to `learning_decision_log` (migration 023).

→ see: 04 (agents — dispatch contract), 06 (data — tables), 07 (external — source clients)
