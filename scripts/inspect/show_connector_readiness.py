from __future__ import annotations

import argparse
import json

from stormready_v3.connectors.factory import build_connector_registry
from stormready_v3.connectors.readiness import ConnectorReadinessService
from stormready_v3.storage.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Show connector readiness for an operator.")
    parser.add_argument("operator_id")
    parser.add_argument("--mode", default=None, help="Connector mode override: snapshot, live, hybrid")
    args = parser.parse_args()

    db = Database()
    db.initialize()
    registry = build_connector_registry(mode=args.mode)
    rows = ConnectorReadinessService(db, registry).build_rows(args.operator_id)
    print(json.dumps(rows, default=str, indent=2))
    db.close()


if __name__ == "__main__":
    main()
