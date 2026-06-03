from __future__ import annotations

import argparse
from pathlib import Path
import pprint

from stormready_v3.notifications.service import list_pending_notifications
from stormready_v3.storage.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List pending StormReady V3 notifications.")
    parser.add_argument("--operator-id", dest="operator_id", default=None, help="Optional operator id filter")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(__file__).resolve().parents[2]
    db = Database(
        db_path=workspace / "runtime_data" / "local" / "stormready_v3.duckdb",
        migrations_root=workspace / "db" / "migrations",
    )
    db.initialize()
    pending = list_pending_notifications(db, operator_id=args.operator_id)
    pprint.pprint(pending)
    db.close()


if __name__ == "__main__":
    main()
