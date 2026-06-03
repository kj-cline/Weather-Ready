from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator
from stormready_v3.orchestration.supervisor import SupervisorService
from stormready_v3.storage.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a StormReady V3 supervisor tick.")
    parser.add_argument("--now", dest="now", default=None, help="UTC timestamp in ISO format")
    parser.add_argument("--skip-queue", action="store_true", help="Skip queued operator refresh requests")
    parser.add_argument("--skip-scheduled", action="store_true", help="Skip scheduled refresh evaluation")
    parser.add_argument("--skip-event-mode", action="store_true", help="Skip event-mode refresh evaluation")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(__file__).resolve().parents[2]
    db = Database(
        db_path=workspace / "runtime_data" / "local" / "stormready_v3.duckdb",
        migrations_root=workspace / "db" / "migrations",
    )
    orchestrator = DeterministicOrchestrator(db)
    orchestrator.initialize()
    supervisor = SupervisorService(orchestrator)
    now = datetime.fromisoformat(args.now).astimezone(UTC) if args.now else datetime.now(UTC)
    result = supervisor.run_tick(
        now=now,
        process_queue=not args.skip_queue,
        process_scheduled=not args.skip_scheduled,
        process_event_mode=not args.skip_event_mode,
    )
    print(
        {
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
            "processed_operator_ids": result.processed_operator_ids,
            "queued_requests_completed": result.queued_requests_completed,
            "scheduled_runs": result.scheduled_runs,
            "event_mode_runs": result.event_mode_runs,
            "skipped_due_to_recent_run": result.skipped_due_to_recent_run,
        }
    )
    db.close()


if __name__ == "__main__":
    main()
