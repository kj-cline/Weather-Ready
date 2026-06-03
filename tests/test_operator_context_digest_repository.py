from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorContextDigestRepository


class OperatorContextDigestRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "operator_context_digest_test.duckdb"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()
        self.repo = OperatorContextDigestRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def test_insert_digest_prunes_to_latest_100_per_operator_and_kind(self) -> None:
        base = datetime(2026, 4, 14, 12, 0, 0)
        for offset in range(105):
            self.repo.insert_digest(
                operator_id="operator_1",
                kind="current_state",
                produced_at=base + timedelta(seconds=offset),
                source_hash=f"hash-{offset}",
                payload_json=f'{{"offset": {offset}}}',
                agent_run_id=None,
            )

        rows = self.db.fetchall(
            """
            SELECT produced_at
            FROM operator_context_digest
            WHERE operator_id = ? AND kind = ?
            ORDER BY produced_at ASC
            """,
            ["operator_1", "current_state"],
        )

        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0][0], base + timedelta(seconds=5))
        self.assertEqual(rows[-1][0], base + timedelta(seconds=104))


if __name__ == "__main__":
    unittest.main()
