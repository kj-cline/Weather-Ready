from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np

from stormready_v3.domain.enums import DemandMix, ForecastRegime, HorizonMode, NeighborhoodType, PredictionCase, ServiceWindow
from stormready_v3.domain.models import LocationContextProfile, OperatorProfile, PredictionContext, ResolvedServiceState, ResolvedTarget
from stormready_v3.prediction.engine import run_forecast
from stormready_v3.reference.brooklyn import load_brooklyn_reference_bundle


def mae(actuals: list[float], predictions: list[float]) -> float:
    y = np.asarray(actuals, dtype=float)
    p = np.asarray(predictions, dtype=float)
    return float(np.mean(np.abs(y - p))) if len(y) else 0.0


def rmse(actuals: list[float], predictions: list[float]) -> float:
    y = np.asarray(actuals, dtype=float)
    p = np.asarray(predictions, dtype=float)
    return float(np.sqrt(np.mean((y - p) ** 2))) if len(y) else 0.0


def build_context(*, service_date: date, baseline_total_covers: int, reference_feature_vector: dict[str, float] | None) -> PredictionContext:
    profile = OperatorProfile(
        operator_id="brooklyn_benchmark",
        restaurant_name="Brooklyn Reference Benchmark",
        primary_service_window=ServiceWindow.DINNER,
        active_service_windows=[ServiceWindow.DINNER],
        neighborhood_type=NeighborhoodType.RESIDENTIAL,
        demand_mix=DemandMix.MIXED,
        indoor_seat_capacity=90,
    )
    location = LocationContextProfile(
        operator_id="brooklyn_benchmark",
        neighborhood_archetype=NeighborhoodType.RESIDENTIAL,
    )
    return PredictionContext(
        operator_profile=profile,
        location_context=location,
        service_date=service_date,
        service_window=ServiceWindow.DINNER,
        resolved_target=ResolvedTarget(target_name="uc_reference_proxy"),
        resolved_service_state=ResolvedServiceState(),
        prediction_case=PredictionCase.BASIC_PROFILE,
        forecast_regime=ForecastRegime.PROFILED_COLD_START,
        horizon_mode=HorizonMode.NEAR,
        baseline_total_covers=baseline_total_covers,
        reference_feature_vector=reference_feature_vector,
    )


def main() -> None:
    bundle = load_brooklyn_reference_bundle()
    rows = bundle.test_df.dropna(subset=bundle.feature_contract + ["UC", "season", "weekday", "date"]).copy()
    actuals: list[float] = []
    baseline_predictions: list[float] = []
    reference_predictions: list[float] = []

    for _, row in rows.iterrows():
        service_date = date.fromisoformat(str(row["date"]))
        season = str(row["season"])
        weekday = int(row["weekday"])
        baseline = int(
            round(
                bundle.season_weekday_baseline_uc.get(
                    (season, weekday),
                    bundle.season_anchor_uc.get(season, 0.0),
                )
            )
        )
        reference_vector = {
            name: float(row[name])
            for name in bundle.feature_contract
        }
        baseline_state, _ = run_forecast(
            build_context(
                service_date=service_date,
                baseline_total_covers=baseline,
                reference_feature_vector=None,
            )
        )
        reference_state, _ = run_forecast(
            build_context(
                service_date=service_date,
                baseline_total_covers=baseline,
                reference_feature_vector=reference_vector,
            )
        )
        actuals.append(float(row["UC"]))
        baseline_predictions.append(float(baseline_state.forecast_expected))
        reference_predictions.append(float(reference_state.forecast_expected))

    payload = {
        "benchmark_scope": "v3_engine_uc_proxy_against_brooklyn_holdout",
        "n_rows": len(actuals),
        "baseline_only": {
            "mae": mae(actuals, baseline_predictions),
            "rmse": rmse(actuals, baseline_predictions),
            "mean_prediction": float(np.mean(baseline_predictions)) if baseline_predictions else 0.0,
        },
        "brooklyn_reference_enabled": {
            "mae": mae(actuals, reference_predictions),
            "rmse": rmse(actuals, reference_predictions),
            "mean_prediction": float(np.mean(reference_predictions)) if reference_predictions else 0.0,
        },
    }
    output_path = Path(__file__).resolve().parents[2] / "reference_assets" / "brooklyn" / "v3_benchmark_results.json"
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
