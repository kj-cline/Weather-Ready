#!/usr/bin/env bash
# StormReady V3 — generic local launcher
# Usage: ./run.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

VENV="$DIR/.venv"
FRONTEND_DIR="$DIR/frontend"
ENV_FILE="$DIR/.env"
ENV_EXAMPLE="$DIR/.env.example"

# --- Python check ---
PYTHON=""
for candidate in python3.12 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major="${version%%.*}"
        minor="${version#*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 12 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python 3.12+ required. Found none."
    exit 1
fi

# --- Venv setup ---
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV"
fi

echo "Syncing dependencies..."
"$VENV/bin/pip" install --quiet --upgrade pip setuptools wheel
"$VENV/bin/pip" install --quiet --no-build-isolation -e ".[ai]"
echo "Environment ready."

if ! command -v npm &>/dev/null; then
    echo "Error: npm is required to run the React frontend."
    exit 1
fi

echo "Syncing frontend dependencies..."
npm install --prefix "$FRONTEND_DIR" --silent
echo "Frontend ready."

# --- .env check ---
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        echo "Created .env from .env.example — edit it with your API keys:"
        echo "  $ENV_FILE"
        echo ""
        echo "Optional for model-backed AI features:"
        echo "  STORMREADY_V3_AGENT_MODEL_PROVIDER=auto|openai|azure"
        echo ""
        echo "OpenAI-style:"
        echo "  STORMREADY_V3_OPENAI_API_KEY"
        echo "  STORMREADY_V3_OPENAI_MODEL"
        echo "  STORMREADY_V3_OPENAI_BASE_URL   # optional"
        echo ""
        echo "Azure-style:"
        echo "  AZURE_OPENAI_API_KEY"
        echo "  AZURE_OPENAI_ENDPOINT"
        echo "  AZURE_OPENAI_API_VERSION"
        echo "  AZURE_OPENAI_DEPLOYMENT"
        echo ""
        echo "The app now requires a valid AI provider configuration."
        echo "Run ./run.sh again after editing .env"
        exit 0
    fi
fi

# --- Launch ---
echo "Starting StormReady V3 (generic local runtime)..."
exec "$VENV/bin/python" "$DIR/scripts/ops/start_local_stack.py" --ui --init-db-if-missing --open-browser
