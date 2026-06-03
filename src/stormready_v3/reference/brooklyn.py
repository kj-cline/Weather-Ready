from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
import pickle
from datetime import date
from typing import Any, Mapping

import numpy as np
import pandas as pd


BROOKLYN_REFERENCE_DIR = Path(__file__).resolve().parents[3] / "reference_assets" / "brooklyn"
BROOKLYN_TRAIN_PATH = BROOKLYN_REFERENCE_DIR / "df_train.csv"
BROOKLYN_TEST_PATH = BROOKLYN_REFERENCE_DIR / "df_test.csv"
BROOKLYN_ANCHOR_PATH = BROOKLYN_REFERENCE_DIR / "stormready_uc_anchor.pkl"
DEFAULT_REFERENCE_MODEL_NAME = "brooklyn_delta_uc_v1"


@dataclass(slots=True)
class BrooklynStrategyMetrics:
    name: str
    n_rows: int
    mae: float
    rmse: float
    mean_prediction: float
    mean_actual: float


@dataclass(slots=True)
class BrooklynReferenceEvaluation:
    baseline: BrooklynStrategyMetrics
    brooklyn_absolute: BrooklynStrategyMetrics
    brooklyn_delta_on_baseline: BrooklynStrategyMetrics
    baseline_by_season: list[dict[str, Any]]
    brooklyn_absolute_by_season: list[dict[str, Any]]
    brooklyn_delta_by_season: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": asdict(self.baseline),
            "brooklyn_absolute": asdict(self.brooklyn_absolute),
            "brooklyn_delta_on_baseline": asdict(self.brooklyn_delta_on_baseline),
            "baseline_by_season": self.baseline_by_season,
            "brooklyn_absolute_by_season": self.brooklyn_absolute_by_season,
            "brooklyn_delta_by_season": self.brooklyn_delta_by_season,
        }


@dataclass(slots=True)
class BrooklynReferenceBundle:
    uc_models: dict[str, Any]
    feature_contract: list[str]
    meta: dict[str, Any]
    season_anchor_uc: dict[str, float]
    season_weekday_baseline_uc: dict[tuple[str, int], float]
    train_df: pd.DataFrame
    test_df: pd.DataFrame


@dataclass(slots=True)
class ReferenceRuntimeOverride:
    bundle: BrooklynReferenceBundle
    model_name: str
    similarity_override: float | None = None


_ACTIVE_REFERENCE_OVERRIDE: ContextVar[ReferenceRuntimeOverride | None] = ContextVar(
    "stormready_active_reference_override",
    default=None,
)


def _empty_reference_bundle(*, reason: str) -> BrooklynReferenceBundle:
    return BrooklynReferenceBundle(
        uc_models={},
        feature_contract=[],
        meta={"load_error": reason},
        season_anchor_uc={},
        season_weekday_baseline_uc={},
        train_df=pd.DataFrame(),
        test_df=pd.DataFrame(),
    )


def active_reference_override() -> ReferenceRuntimeOverride | None:
    return _ACTIVE_REFERENCE_OVERRIDE.get()


def active_reference_model_name() -> str:
    override = active_reference_override()
    if override is None:
        return DEFAULT_REFERENCE_MODEL_NAME
    return str(override.model_name or DEFAULT_REFERENCE_MODEL_NAME)


@contextmanager
def use_reference_override(override: ReferenceRuntimeOverride | None):
    token = _ACTIVE_REFERENCE_OVERRIDE.set(override)
    try:
        yield
    finally:
        _ACTIVE_REFERENCE_OVERRIDE.reset(token)


def _cloudcover_bin(cloudcover_mean: float | None) -> int:
    value = float(cloudcover_mean or 0.0)
    if value < 25:
        return 0
    if value < 50:
        return 1
    if value < 75:
        return 2
    return 3


def _prepare_brooklyn_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "cloudcover_bin" not in out.columns and "cloudcover_dinner_mean" in out.columns:
        out["cloudcover_bin"] = out["cloudcover_dinner_mean"].apply(_cloudcover_bin)
    if "year_code" not in out.columns:
        out["year_code"] = 1
    return out


def _require_reference_assets() -> None:
    missing = [
        path.name
        for path in (BROOKLYN_TRAIN_PATH, BROOKLYN_TEST_PATH, BROOKLYN_ANCHOR_PATH)
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError(f"Missing Brooklyn reference assets: {', '.join(missing)}")


@lru_cache(maxsize=1)
def load_brooklyn_reference_bundle() -> BrooklynReferenceBundle:
    _require_reference_assets()
    try:
        with BROOKLYN_ANCHOR_PATH.open("rb") as handle:
            raw = pickle.load(handle)
    except ModuleNotFoundError as exc:
        # The transferred reference pickle was serialized with pygam models.
        # Missing pygam should disable the optional reference layer, not block refresh.
        return _empty_reference_bundle(reason=f"missing_optional_dependency:{exc.name}")
    train_df = _prepare_brooklyn_frame(pd.read_csv(BROOKLYN_TRAIN_PATH))
    test_df = _prepare_brooklyn_frame(pd.read_csv(BROOKLYN_TEST_PATH))
    feature_contract = list(raw.get("meta", {}).get("brooklyn_uc_feat", []))
    season_anchor_uc = (
        train_df.groupby("season", dropna=False)["UC"].median().astype(float).to_dict()
        if "season" in train_df.columns and "UC" in train_df.columns
        else {}
    )
    season_weekday_baseline_uc = (
        train_df.groupby(["season", "weekday"], dropna=False)["UC"].median().astype(float).to_dict()
        if {"season", "weekday", "UC"}.issubset(train_df.columns)
        else {}
    )
    return BrooklynReferenceBundle(
        uc_models=raw.get("uc_models", {}),
        feature_contract=feature_contract,
        meta=raw.get("meta", {}),
        season_anchor_uc=season_anchor_uc,
        season_weekday_baseline_uc=season_weekday_baseline_uc,
        train_df=train_df,
        test_df=test_df,
    )


def build_brooklyn_reference_features(raw: Mapping[str, Any]) -> dict[str, float] | None:
    override = active_reference_override()
    bundle = override.bundle if override is not None else load_brooklyn_reference_bundle()
    features: dict[str, float] = {}
    for name in bundle.feature_contract:
        if name == "cloudcover_bin":
            if name in raw:
                features[name] = float(raw[name])
                continue
            if "cloudcover_dinner_mean" in raw:
                features[name] = float(_cloudcover_bin(raw.get("cloudcover_dinner_mean")))
                continue
        if name == "year_code" and name not in raw:
            features[name] = 1.0
            continue
        value = raw.get(name)
        if value is None:
            return None
        features[name] = float(value)
    return features


def season_from_service_date(service_date: date) -> str:
    month = service_date.month
    day = service_date.day
    if (month == 3 and day >= 20) or month in {4, 5} or (month == 6 and day <= 20):
        return "Spring"
    if (month == 6 and day >= 21) or month in {7, 8} or (month == 9 and day <= 21):
        return "Summer"
    if (month == 9 and day >= 22) or month in {10, 11} or (month == 12 and day <= 20):
        return "Fall"
    return "Winter"


def brooklyn_absolute_uc_prediction(
    *,
    season: str,
    feature_vector: Mapping[str, float],
    bundle: BrooklynReferenceBundle | None = None,
) -> float | None:
    override = active_reference_override()
    reference = bundle or (override.bundle if override is not None else load_brooklyn_reference_bundle())
    model = reference.uc_models.get(season)
    if model is None:
        return None
    try:
        x = np.array([[float(feature_vector[name]) for name in reference.feature_contract]], dtype=float)
        return float(model.predict(x)[0])
    except Exception:
        return None


def brooklyn_delta_uc_prediction(
    *,
    season: str,
    baseline_uc: float,
    feature_vector: Mapping[str, float],
    bundle: BrooklynReferenceBundle | None = None,
) -> float | None:
    override = active_reference_override()
    reference = bundle or (override.bundle if override is not None else load_brooklyn_reference_bundle())
    absolute_prediction = brooklyn_absolute_uc_prediction(
        season=season,
        feature_vector=feature_vector,
        bundle=reference,
    )
    if absolute_prediction is None:
        return None
    season_anchor = float(reference.season_anchor_uc.get(season, baseline_uc))
    return float(baseline_uc + (absolute_prediction - season_anchor))


def _strategy_metrics(name: str, actuals: list[float], predictions: list[float]) -> BrooklynStrategyMetrics:
    y = np.asarray(actuals, dtype=float)
    p = np.asarray(predictions, dtype=float)
    return BrooklynStrategyMetrics(
        name=name,
        n_rows=int(len(y)),
        mae=float(np.mean(np.abs(y - p))) if len(y) else 0.0,
        rmse=float(np.sqrt(np.mean((y - p) ** 2))) if len(y) else 0.0,
        mean_prediction=float(np.mean(p)) if len(p) else 0.0,
        mean_actual=float(np.mean(y)) if len(y) else 0.0,
    )


def _season_breakdown(
    *,
    seasons: list[str],
    actuals: list[float],
    predictions: list[float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    unique_seasons = sorted(set(seasons))
    for season in unique_seasons:
        indices = [index for index, season_name in enumerate(seasons) if season_name == season]
        season_actuals = [actuals[index] for index in indices]
        season_predictions = [predictions[index] for index in indices]
        metrics = _strategy_metrics(season, season_actuals, season_predictions)
        rows.append(asdict(metrics))
    return rows


def evaluate_brooklyn_reference() -> BrooklynReferenceEvaluation:
    bundle = load_brooklyn_reference_bundle()
    test_df = bundle.test_df.dropna(subset=bundle.feature_contract + ["UC"]).copy()

    baseline_predictions: list[float] = []
    brooklyn_absolute_predictions: list[float] = []
    brooklyn_delta_predictions: list[float] = []
    actuals: list[float] = []
    seasons: list[str] = []

    for _, row in test_df.iterrows():
        season = str(row["season"])
        weekday = int(row["weekday"])
        baseline_uc = float(
            bundle.season_weekday_baseline_uc.get(
                (season, weekday),
                bundle.season_anchor_uc.get(season, 0.0),
            )
        )
        feature_vector = {name: float(row[name]) for name in bundle.feature_contract}
        absolute_prediction = brooklyn_absolute_uc_prediction(
            season=season,
            feature_vector=feature_vector,
            bundle=bundle,
        )
        delta_prediction = brooklyn_delta_uc_prediction(
            season=season,
            baseline_uc=baseline_uc,
            feature_vector=feature_vector,
            bundle=bundle,
        )
        if absolute_prediction is None or delta_prediction is None:
            continue
        actual = float(row["UC"])
        seasons.append(season)
        actuals.append(actual)
        baseline_predictions.append(baseline_uc)
        brooklyn_absolute_predictions.append(absolute_prediction)
        brooklyn_delta_predictions.append(delta_prediction)

    return BrooklynReferenceEvaluation(
        baseline=_strategy_metrics("season_weekday_baseline", actuals, baseline_predictions),
        brooklyn_absolute=_strategy_metrics("brooklyn_absolute", actuals, brooklyn_absolute_predictions),
        brooklyn_delta_on_baseline=_strategy_metrics("brooklyn_delta_on_baseline", actuals, brooklyn_delta_predictions),
        baseline_by_season=_season_breakdown(
            seasons=seasons,
            actuals=actuals,
            predictions=baseline_predictions,
        ),
        brooklyn_absolute_by_season=_season_breakdown(
            seasons=seasons,
            actuals=actuals,
            predictions=brooklyn_absolute_predictions,
        ),
        brooklyn_delta_by_season=_season_breakdown(
            seasons=seasons,
            actuals=actuals,
            predictions=brooklyn_delta_predictions,
        ),
    )
