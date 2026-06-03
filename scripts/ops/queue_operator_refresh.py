from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from stormready_v3.domain.enums import ServiceWindow
from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator
from stormready_v3.orchestration.supervisor import SupervisorService
from stormready_v3.storage.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Queue an operator-requested refresh.")
    parser.add_argument("operator_id", help="Operator id")
    parser.add_argument("--date", dest="requested_for_date", default=None, help="Requested run date in YYYY-MM-DD format")
    parser.add_argument("--window", dest="service_window", default=None, choices=[window.value for window in ServiceWindow], help="Optional service window")
    parser.add_argument("--note", dest="note", default=None, help="Optional request note")
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
    request_id = supervisor.enqueue_operator_refresh_request(
        operator_id=args.operator_id,
        requested_for_date=date.fromisoformat(args.requested_for_date) if args.requested_for_date else None,
        requested_service_window=ServiceWindow(args.service_window) if args.service_window else None,
        note=args.note,
    )
    print({"request_id": request_id, "operator_id": args.operator_id})
    db.close()


if __name__ == "__main__":
    main()
