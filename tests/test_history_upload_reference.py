from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from stormready_v3.domain.enums import DemandMix, NeighborhoodType
from stormready_v3.imports.history_upload import claim_historical_upload_for_operator, review_and_stage_historical_upload
from stormready_v3.reference.brooklyn import BrooklynReferenceBundle
from stormready_v3.reference.operator_history import (
    load_selected_operator_reference_override,
    train_operator_history_reference_asset,
)
from stormready_v3.setup.service import SetupRequest, SetupService
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository


def _first_weekday_in_month(year: int, month: int, weekday: int) -> date:
    current = date(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current


def _seasonal_weather_payload(service_date: date) -> dict[str, float]:
    month = service_date.month
    weekday = service_date.isoweekday()
    apparent_temp = 42.0 + (month * 2.4) + (weekday * 1.1)
    precip_dinner_max = 0.11 if month in {12, 1, 2} and weekday >= 5 else (0.04 if weekday == 1 else 0.0)
    precip_type_code = 2.0 if month in {12, 1, 2} and precip_dinner_max > 0 else (1.0 if precip_dinner_max > 0 else 0.0)
    cloudcover_bin = 3.0 if month in {12, 1, 2} else (1.0 if month in {6, 7, 8} else 2.0)
    precip_lunch = 0.03 if month in {4, 5, 9, 10} else 0.0
    return {
        "available": True,
        "apparent_temp_7pm": apparent_temp,
        "precip_dinner_max": precip_dinner_max,
        "precip_type_code": precip_type_code,
        "weekday": float(weekday),
        "year_code": 1.0,
        "cloudcover_bin": cloudcover_bin,
        "precip_lunch": precip_lunch,
    }


def _expected_covers_for_date(service_date: date) -> int:
    weekday = service_date.weekday()
    if weekday <= 3:
        baseline = 120
    elif weekday == 4:
        baseline = 150
    elif weekday == 5:
        baseline = 170
    else:
        baseline = 112
    payload = _seasonal_weather_payload(service_date)
    weather_lift = (
        (payload["apparent_temp_7pm"] - 60.0) * 0.6
        - (payload["precip_dinner_max"] * 95.0)
        - (payload["cloudcover_bin"] * 3.0)
        + (payload["precip_lunch"] * 18.0)
    )
    return max(40, int(round(baseline + weather_lift)))


def _history_csv() -> str:
    lines = ["service_date,total_covers"]
    month_cursor = date(2025, 4, 1)
    for offset in range(12):
        year = month_cursor.year + ((month_cursor.month - 1 + offset) // 12)
        month = ((month_cursor.month - 1 + offset) % 12) + 1
        for weekday in (0, 4, 5, 6):  # Mon, Fri, Sat, Sun
            service_date = _first_weekday_in_month(year, month, weekday)
            lines.append(f"{service_date.isoformat()},{_expected_covers_for_date(service_date)}")
    return "\n".join(lines) + "\n"


class _ConstantModel:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.full(shape=(len(x),), fill_value=self.value, dtype=float)


class HistoricalUploadReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temp_dir.name) / "history_upload_reference.duckdb"
        self.runtime_root = Path(self._temp_dir.name) / "runtime"
        self.db = Database(db_path=self.db_path)
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self._temp_dir.cleanup()

    def test_review_and_stage_upload_derives_baselines(self) -> None:
        review = review_and_stage_historical_upload(
            self.db,
            file_name="history.csv",
            content=_history_csv(),
            provider=None,
        )

        self.assertTrue(review.accepted)
        self.assertEqual(review.distinct_months, 12)
        self.assertEqual(sorted(review.seasons_covered), ["Fall", "Spring", "Summer", "Winter"])
        self.assertGreater(review.baseline_values["mon_thu"], 0)
        self.assertGreater(review.baseline_values["fri"], review.baseline_values["mon_thu"])
        self.assertGreater(review.baseline_values["sat"], review.baseline_values["fri"])
        self.assertIn("No reserved-cover split was found", " ".join(review.warnings))

    def test_operator_history_reference_can_replace_brooklyn(self) -> None:
        csv_content = _history_csv()
        review = review_and_stage_historical_upload(
            self.db,
            file_name="history.csv",
            content=csv_content,
            provider=None,
        )

        service = SetupService(OperatorRepository(self.db), geocoder=None, db=self.db, ai_provider=None)
        service.create_or_update_operator(
            SetupRequest(
                operator_id="operator_upload",
                restaurant_name="Upload Test",
                canonical_address="11 W 53rd St, New York, NY 10019",
                city="New York",
                timezone="America/New_York",
                lat=40.7614,
                lon=-73.9776,
                neighborhood_type=NeighborhoodType.MIXED_URBAN,
                demand_mix=DemandMix.MIXED,
                weekly_baselines=review.baseline_values,
                weekly_baseline_source_type="historical_upload",
            ),
            run_enrichment=False,
        )
        claim_historical_upload_for_operator(
            self.db,
            upload_token=review.upload_token,
            operator_id="operator_upload",
        )

        feature_contract = [
            "apparent_temp_7pm",
            "precip_dinner_max",
            "precip_type_code",
            "weekday",
            "year_code",
            "cloudcover_bin",
            "precip_lunch",
        ]
        poor_brooklyn_bundle = BrooklynReferenceBundle(
            uc_models={season: _ConstantModel(35.0) for season in ("Spring", "Summer", "Fall", "Winter")},
            feature_contract=feature_contract,
            meta={"design": "test_bundle"},
            season_anchor_uc={season: 120.0 for season in ("Spring", "Summer", "Fall", "Winter")},
            season_weekday_baseline_uc={},
            train_df=pd.DataFrame(),
            test_df=pd.DataFrame(),
        )

        def fake_build_features(raw: dict[str, float]) -> dict[str, float]:
            return {name: float(raw[name]) for name in feature_contract}

        with (
            patch("stormready_v3.reference.operator_history.fetch_year_archive", return_value={}),
            patch("stormready_v3.reference.operator_history.weather_payload_from_hourly_raw", side_effect=lambda _raw, *, service_date, timezone_name: _seasonal_weather_payload(service_date)),
            patch("stormready_v3.reference.operator_history.build_brooklyn_reference_features", side_effect=fake_build_features),
            patch("stormready_v3.reference.operator_history.load_brooklyn_reference_bundle", return_value=poor_brooklyn_bundle),
        ):
            result = train_operator_history_reference_asset(
                self.db,
                operator_id="operator_upload",
                runtime_root=self.runtime_root,
                cache_root=self.runtime_root / "weather_cache",
            )

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.selected_for_runtime)
        self.assertEqual(result.model_name, "operator_history_delta_uc_v1")
        self.assertIsNotNone(result.bundle_path)
        self.assertTrue(Path(str(result.bundle_path)).exists())
        self.assertEqual(result.benchmark["selection_reason"], "operator_history_beats_brooklyn_insample")

        override = load_selected_operator_reference_override(self.db, operator_id="operator_upload")
        self.assertIsNotNone(override)
        self.assertEqual(override.model_name, "operator_history_delta_uc_v1")
        self.assertEqual(override.similarity_override, 1.0)


if __name__ == "__main__":
    unittest.main()
