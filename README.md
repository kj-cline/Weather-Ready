# WeatherReady

WeatherReady is a weather-aware dinner forecasting system for independent
restaurants. It predicts cover counts, explains the likely drivers in plain
language, and learns from operator feedback over time.

The local runtime is a compact monolith:

- FastAPI backend
- React 18 + TypeScript frontend built with Vite
- Embedded DuckDB database
- Azure OpenAI or OpenAI-compatible model-backed agents

## Repository Layout

```text
src/stormready_v3/      Python backend package
frontend/               React/Vite frontend
db/migrations/          DuckDB schema migrations
scripts/                Local operations and inspection helpers
tests/                  Backend tests
knowledge-base/         Architecture notes for maintainers
```

Generated local state is intentionally not committed. This includes `.venv/`,
`node_modules/`, `frontend/node_modules/`, `frontend/dist/`, `runtime_data/`,
`.env`, caches, logs, and local DuckDB files.

## Requirements

- Python 3.12+
- Node.js and npm
- Azure OpenAI credentials, or an OpenAI/OpenAI-compatible provider

Python dependencies are declared in `pyproject.toml`. The included
`requirements.txt` contains `-e .[ai]` so pip-based environments install from
the same project metadata instead of maintaining a second dependency list.

Frontend dependencies are pinned by `frontend/package-lock.json`.

## Configuration

Create a local `.env` from the example:

```bash
cp .env.example .env
```

The default team path is Azure OpenAI:

```env
STORMREADY_V3_SOURCE_MODE=live
STORMREADY_V3_AGENT_MODEL_PROVIDER=azure

AZURE_OPENAI_API_KEY=
AZURE_OPENAI_DEPLOYMENT=gpt-5.3-chat
AZURE_OPENAI_ENDPOINT=https://<your-resource-name>.openai.azure.com
AZURE_OPENAI_API_VERSION=2025-04-01-preview
```

Fill in `AZURE_OPENAI_API_KEY` and the real Azure resource endpoint locally.
Do not commit `.env`.

For OpenAI or an OpenAI-compatible endpoint, set:

```env
STORMREADY_V3_AGENT_MODEL_PROVIDER=openai
STORMREADY_V3_OPENAI_API_KEY=
STORMREADY_V3_OPENAI_MODEL=gpt-4o-mini
STORMREADY_V3_OPENAI_BASE_URL=
```

`STORMREADY_V3_OPENAI_BASE_URL` must be compatible with the OpenAI SDK
chat-completions API and JSON response formatting.

## Quick Start

The generic launcher prepares Python and frontend dependencies, creates `.env`
from `.env.example` if needed, initializes the database when needed, and starts
the local stack:

```bash
./run.sh
```

On the first run, if `.env` does not exist, the launcher creates it and exits so
you can add provider credentials. Run `./run.sh` again after editing `.env`.

## Manual Setup

Use these commands when you want each step explicit:

```bash
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip setuptools wheel
.venv/bin/pip install -r requirements.txt
npm ci --prefix frontend
PYTHONPATH=src .venv/bin/python scripts/ops/init_db.py
PYTHONPATH=src .venv/bin/python scripts/ops/start_local_stack.py --ui --init-db-if-missing
```

The default DuckDB path is `runtime_data/local/stormready_v3.duckdb`. Override
it with `STORMREADY_V3_DB_PATH` when needed.

## Useful Commands

```bash
make show-config
make show-health
make init-db
make test
make local-stack
```

Frontend-only commands:

```bash
npm ci --prefix frontend
npm run build --prefix frontend
npm run test:ui --prefix frontend
npm run test:e2e --prefix frontend
```

## Architecture Notes

StormReady uses a seven-role agent dispatcher inside the backend:

- `conversation_orchestrator`
- `conversation_note_extractor`
- `current_state_retriever`
- `temporal_memory_retriever`
- `signal_interpreter`
- `anomaly_explainer`
- `prediction_governor`

The system is one feedback loop: operator events update shared DuckDB memory,
agents and deterministic services reason over that memory, and the forecast,
chat, and learning surfaces shape the next operator decision.

Start with `knowledge-base/01_system_overview.md` for the end-to-end map, then
use the other files in `knowledge-base/` for frontend, API, agents,
orchestration, data, and external-source context.

## Database Migrations

Schema migrations live in `db/migrations/` and are applied in order on startup.
Do not remove or reorder existing migrations. New schema changes should use the
next sequential migration number.

## Preparing a Fresh GitHub Repo

For a new repository:

```bash
git init
git add .
git status --short
git commit -m "Initial StormReady import"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

Before committing, verify that generated or private files are not staged:

- `.env`
- `.venv/`
- `node_modules/`
- `frontend/node_modules/`
- `frontend/dist/`
- `runtime_data/`
- local DuckDB files
