from __future__ import annotations

import argparse
import json

from stormready_v3.config.settings import DEFAULT_DB_PATH, MIGRATIONS_ROOT
from stormready_v3.storage.db import Database
from stormready_v3.workflows.corrections import list_pending_corrections


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show pending correction suggestions.")
    parser.add_argument("--operator-id", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = Database(db_path=args.db, migrations_root=MIGRATIONS_ROOT)
    db.initialize()
    pending = list_pending_corrections(
        db,
        operator_id=args.operator_id,
        limit=args.limit,
    )
    print(json.dumps(pending, default=str, indent=2))
    db.close()


if __name__ == "__main__":
    main()
