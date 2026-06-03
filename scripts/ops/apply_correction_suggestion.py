from __future__ import annotations

import argparse

from stormready_v3.config.settings import DEFAULT_DB_PATH, MIGRATIONS_ROOT
from stormready_v3.storage.db import Database
from stormready_v3.workflows.corrections import apply_correction_suggestion


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a staged correction suggestion.")
    parser.add_argument("suggestion_id", type=int)
    parser.add_argument("--note", default=None)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = Database(db_path=args.db, migrations_root=MIGRATIONS_ROOT)
    db.initialize()
    applied = apply_correction_suggestion(
        db,
        suggestion_id=args.suggestion_id,
        decision_note=args.note,
    )
    print("applied" if applied else "not_applied")
    db.close()


if __name__ == "__main__":
    main()
