from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import pprint

from stormready_v3.conversation.service import ConversationContextService
from stormready_v3.storage.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show read-only conversation context for an operator.")
    parser.add_argument("operator_id", help="Operator id")
    parser.add_argument("--date", dest="reference_date", default=None, help="Reference date in YYYY-MM-DD format")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(__file__).resolve().parents[2]
    db = Database(
        db_path=workspace / "runtime_data" / "local" / "stormready_v3.duckdb",
        migrations_root=workspace / "db" / "migrations",
    )
    db.initialize()
    service = ConversationContextService(db)
    reference_date = date.fromisoformat(args.reference_date) if args.reference_date else date.today()
    context = service.build_context(operator_id=args.operator_id, reference_date=reference_date)
    pprint.pprint(
        {
            "operator_id": context.operator_id,
            "actionable_forecasts": context.actionable_forecasts,
            "actionable_components": context.actionable_components,
            "confidence_calibration": context.confidence_calibration,
            "recent_connector_truths": context.recent_connector_truths,
            "recent_notes": context.recent_notes,
            "recent_snapshots": context.recent_snapshots,
            "recent_evaluations": context.recent_evaluations,
            "operator_preferences": context.operator_preferences,
        }
    )
    db.close()


if __name__ == "__main__":
    main()
