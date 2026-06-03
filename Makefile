PYTHON ?= .venv/bin/python
PYTHONPATH_VALUE := src
OPERATOR_ID ?= a2b

.PHONY: init-db test frontend supervisor-once refresh-once show-config show-health connector-readiness local-stack

init-db:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/ops/init_db.py

test:
	@if [ -d tests ]; then \
		PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) -m pytest tests -v; \
	else \
		echo "No tests/ directory present in this checkout."; \
	fi

frontend:
	./run.sh

supervisor-once:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/ops/run_supervisor_loop.py --once

refresh-once:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/ops/run_refresh_cycle.py --date 2026-04-05 --reason scheduled --window morning

show-config:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/inspect/show_runtime_config.py

show-health:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/inspect/show_runtime_health.py

connector-readiness:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/inspect/show_connector_readiness.py $(OPERATOR_ID)

local-stack:
	PYTHONPATH=$(PYTHONPATH_VALUE) $(PYTHON) scripts/ops/start_local_stack.py --show-config --ui --supervisor-loop
