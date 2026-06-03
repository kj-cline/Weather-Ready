from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from stormready_v3.config.settings import DEFAULT_DB_PATH, MIGRATIONS_ROOT
from stormready_v3.monitoring.health import RuntimeHealthService
from stormready_v3.storage.db import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show StormReady V3 runtime health summary.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = Database(db_path=args.db, migrations_root=MIGRATIONS_ROOT)
    db.initialize()
    summary = RuntimeHealthService(db).build_summary()
    print(json.dumps(asdict(summary), default=str, indent=2))
    db.close()


if __name__ == "__main__":
    main()
