from __future__ import annotations

from stormready_v3.storage.db import Database


def main() -> None:
    db = Database()
    db.initialize()
    print(f"Initialized StormReady V3 DB at {db.db_path}")


if __name__ == "__main__":
    main()
