from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from stormready_v3.domain.enums import RefreshReason
from stormready_v3.orchestration.orchestrator import DeterministicOrchestrator
from stormready_v3.orchestration.refresh_service import RefreshService
from stormready_v3.storage.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a StormReady V3 refresh cycle for all active operators.")
    parser.add_argument("--date", dest="run_date", default=None, help="Run date in YYYY-MM-DD format")
    parser.add_argument(
        "--reason",
        dest="reason",
        default=RefreshReason.SCHEDULED.value,
        choices=[reason.value for reason in RefreshReason],
        help="Refresh reason",
    )
    parser.add_argument(
        "--window",
        dest="refresh_window",
        default="morning",
        choices=["morning", "midday", "pre_dinner"],
        help="Scheduled refresh window",
    )
    parser.add_argument(
        "--event-mode",
        dest="event_mode_active",
        action="store_true",
        help="Mark the run as event mode active",
    )
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
    service = RefreshService(orchestrator)
    run_date = date.fromisoformat(args.run_date) if args.run_date else date.today()
    result = service.run_for_all_active_operators(
        refresh_reason=RefreshReason(args.reason),
        run_date=run_date,
        refresh_window=args.refresh_window,
        event_mode_active=args.event_mode_active,
    )
    print(
        {
            "run_date": str(run_date),
            "refresh_reason": args.reason,
            "refresh_window": args.refresh_window,
            "event_mode_active": args.event_mode_active,
            "operators_processed": result.operators_processed,
        }
    )
    db.close()


if __name__ == "__main__":
    main()
