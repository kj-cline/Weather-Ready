# 07 — External World

Everything outside the Python process: weather, signal sources, and the LLM
provider.

## Weather

- `sources/open_meteo.py` — Open-Meteo forecast API (primary)
- `sources/nws.py` — NOAA NWS alerts
- `sources/weather_archive.py` — local archive for replay and tests
- `sources/mock.py` — deterministic mock for offline runs

## Signal sources

- **Capital Bikeshare GBFS** — transit proxy; parsed as a disruption signal
- **OpenTable partner API** (env-gated) — reservation context
- **Toast POS** (env-gated) — cover data
- Normalization + contracts live in `sources/` and `connectors/`

## Connectors

`connectors/` wraps signal sources behind typed interfaces:

- `connectors/registry.py` — source registration
- `connectors/mapping.py` — operator → connector resolution
- `connectors/contracts.py` — typed connector interfaces
- `connectors/readiness.py` — pre-fetch health checks

## LLM provider

- `ai/openai_provider.py` — Azure OpenAI SDK wrapper
- `ai/contracts.py::AgentModelProvider` — protocol implemented by the wrapper
- Supports structured-JSON and chat modes
- `provider.is_available()` guards every AI call
- Built once in FastAPI lifespan; shared by the dispatcher and the three ad-hoc AI sites (see node 04)

## Runtime modes

Controlled in `config/settings.py`:

- `STORMREADY_V3_SOURCE_MODE` — `live` / `mock` / `hybrid` / `detailed_mock`
- `STORMREADY_V3_CONNECTOR_MODE` — `snapshot` / `default` / `hybrid` / `live`
- `STORMREADY_V3_AGENT_MODEL_PROVIDER` — `azure` / `openai` / `local` / `mock`

Env vars: `AZURE_OPENAI_*`, NWS + Open-Meteo URLs, OpenTable + Toast keys.

## Horizons + windows

- `ACTIONABLE_HORIZON_DAYS = 14`
- `WORKING_HORIZON_DAYS = 21`
- `NOTIFICATION_HORIZON_DAYS = 3`
- `SCHEDULED_REFRESH_WINDOWS = ("morning", "midday", "pre_dinner")`

→ see: 05 (orchestration — who calls these clients), 04 (agents — LLM usage)
