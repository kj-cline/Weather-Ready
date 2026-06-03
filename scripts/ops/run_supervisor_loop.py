from __future__ import annotations

import argparse
import time

from stormready_v3.config.settings import DEFAULT_DB_PATH, MIGRATIONS_ROOT
from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator
from stormready_v3.orchestration.supervisor import SupervisorService
from stormready_v3.storage.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the StormReady V3 supervisor loop.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    while True:
        db = Database(db_path=args.db, migrations_root=MIGRATIONS_ROOT)
        orchestrator = DeterministicOrchestrator(db)
        orchestrator.initialize()
        supervisor = SupervisorService(orchestrator)
        result = supervisor.run_tick()
        db.close()
        print(
            f"tick completed operators={len(result.processed_operator_ids)} "
            f"queue={result.queued_requests_completed} scheduled={result.scheduled_runs} event={result.event_mode_runs}"
        )
        if args.once:
            break
        time.sleep(max(1, args.interval_seconds))


if __name__ == "__main__":
    main()
