from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
import pickle
import uuid
from typing import Any

import numpy as np
import pandas as pd

from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.imports.history_upload import HistoricalUploadRow, load_operator_historical_upload_rows
from stormready_v3.reference.brooklyn import (
    BrooklynReferenceBundle,
    BrooklynStrategyMetrics,
    ReferenceRuntimeOverride,
    brooklyn_delta_uc_prediction,
    build_brooklyn_reference_features,
    load_brooklyn_reference_bundle,
    season_from_service_date,
)
from stormready_v3.sources.open_meteo import weather_payload_from_hourly_raw
from stormready_v3.sources.weather_archive import fetch_year_archive
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorRepository


DEFAULT_OPERATOR_REFERENCE_MODEL_NAME = "operator_history_delta_uc_v1"
_SEASONS = ("Spring", "Summer", "Fall", "Winter")


@dataclass(slots=True)
class OperatorReferenceTrainingResult:
    status: str
    message: str
    selected_for_runtime: bool
    model_name: str | None = None
    asset_id: str | None = None
    upload_token: str | None = None
    benchmark: dict[str, Any] | None = None
    bundle_path: str | None = None


def _db_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _day_group_for_date(service_date: date) -> str:
    weekday = service_date.weekday()
    if weekday <= 3:
        return "mon_thu"
    if weekday == 4:
        return "fri"
    if weekday == 5:
        return "sat"
    return "sun"


def _asset_root(runtime_root: Path) -> Path:
    return runtime_root / "reference_assets" / "operator_history"


def _metrics(name: str, actuals: list[float], predictions: list[float]) -> BrooklynStrategyMetrics:
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


def _build_operator_bundle(
    *,
    feature_contract: list[str],
    models_by_season: dict[str, Any],
    rows: pd.DataFrame,
    upload_token: str,
    operator_id: str,
) -> BrooklynReferenceBundle:
    season_anchor_uc = (
        rows.groupby("season", dropna=False)["target_covers"].median().astype(float).to_dict()
        if not rows.empty
        else {}
    )
    season_weekday_baseline_uc = (
        rows.groupby(["season", "weekday"], dropna=False)["target_covers"].median().astype(float).to_dict()
        if not rows.empty
        else {}
    )
    return BrooklynReferenceBundle(
        uc_models=models_by_season,
        feature_contract=list(feature_contract),
        meta={
            "design": "operator_history_uc_anchor_v1",
            "source_upload_token": upload_token,
            "operator_id": operator_id,
            "trained_at": _db_now().isoformat(),
        },
        season_anchor_uc=season_anchor_uc,
        season_weekday_baseline_uc=season_weekday_baseline_uc,
        train_df=rows.copy(),
        test_df=rows.copy(),
    )


def _load_bundle_from_path(bundle_path: str) -> BrooklynReferenceBundle:
    with Path(bundle_path).open("rb") as handle:
        raw = pickle.load(handle)
    if isinstance(raw, BrooklynReferenceBundle):
        return raw
    if not isinstance(raw, dict):
        raise ValueError("Reference bundle file is invalid.")
    return BrooklynReferenceBundle(
        uc_models=dict(raw.get("uc_models") or {}),
        feature_contract=[str(item) for item in list(raw.get("feature_contract") or [])],
        meta=dict(raw.get("meta") or {}),
        season_anchor_uc={str(key): float(value) for key, value in dict(raw.get("season_anchor_uc") or {}).items()},
        season_weekday_baseline_uc={
            (str(key[0]), int(key[1])): float(value)
            for key, value in dict(raw.get("season_weekday_baseline_uc") or {}).items()
        },
        train_df=pd.DataFrame(raw.get("train_df") or []),
        test_df=pd.DataFrame(raw.get("test_df") or []),
    )


def _store_bundle(bundle: BrooklynReferenceBundle, *, bundle_path: Path) -> None:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "uc_models": bundle.uc_models,
        "feature_contract": bundle.feature_contract,
        "meta": bundle.meta,
        "season_anchor_uc": bundle.season_anchor_uc,
        "season_weekday_baseline_uc": bundle.season_weekday_baseline_uc,
        "train_df": bundle.train_df.to_dict(orient="records"),
        "test_df": bundle.test_df.to_dict(orient="records"),
    }
    with bundle_path.open("wb") as handle:
        pickle.dump(payload, handle)


def _build_training_frame(
    rows: list[HistoricalUploadRow],
    *,
    lat: float,
    lon: float,
    timezone_name: str,
    cache_root: Path | None,
) -> pd.DataFrame:
    feature_contract = list(load_brooklyn_reference_bundle().feature_contract)
    archives_by_year: dict[int, dict[str, Any]] = {}
    training_rows: list[dict[str, Any]] = []
    for row in rows:
        service_date = date.fromisoformat(row.service_date)
        if row.service_window != ServiceWindow.DINNER.value:
            continue
        if row.service_state != ServiceState.NORMAL.value:
            continue
        archive = archives_by_year.get(service_date.year)
        if archive is None:
            archive = fetch_year_archive(
                lat=lat,
                lon=lon,
                year=service_date.year,
                timezone=timezone_name,
                cache_root=cache_root,
            )
            archives_by_year[service_date.year] = archive
        payload = weather_payload_from_hourly_raw(
            archive,
            service_date=service_date,
            timezone_name=timezone_name,
        )
        if not payload.get("available"):
            continue
        feature_vector = build_brooklyn_reference_features(payload)
        if feature_vector is None:
            continue
        training_row = {
            "service_date": service_date.isoformat(),
            "season": season_from_service_date(service_date),
            "weekday": int(service_date.isoweekday()),
            "day_group": _day_group_for_date(service_date),
            "target_covers": float(row.realized_total_covers),
        }
        for feature_name in feature_contract:
            training_row[feature_name] = float(feature_vector[feature_name])
        training_rows.append(training_row)
    frame = pd.DataFrame(training_rows)
    if not frame.empty:
        day_group_baselines = (
            frame.groupby("day_group", dropna=False)["target_covers"].mean().round().astype(float).to_dict()
        )
        frame["baseline_total_covers"] = frame["day_group"].map(day_group_baselines).astype(float)
    return frame


def _fit_operator_models(rows: pd.DataFrame, feature_contract: list[str]) -> dict[str, Any]:
    try:
        from pygam import LinearGAM, f, s
    except Exception as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError(f"missing_pygam:{exc}") from exc

    models: dict[str, Any] = {}
    terms = s(0, n_splines=8) + s(1, n_splines=6) + f(2) + f(3) + f(5) + s(6, n_splines=6)
    lam_grid = np.array([0.1, 1.0, 10.0])
    for season_name in _SEASONS:
        season_rows = rows[rows["season"] == season_name].copy()
        if len(season_rows) < 8:
            raise RuntimeError(f"season_rows_too_sparse:{season_name}")
        x = season_rows[feature_contract].astype(float).to_numpy()
        y = season_rows["target_covers"].astype(float).to_numpy()
        model = LinearGAM(terms)
        try:
            model.gridsearch(x, y, lam=lam_grid, progress=False)
        except Exception:
            model.fit(x, y)
        models[season_name] = model
    return models


def _benchmark_reference_bundle(
    rows: pd.DataFrame,
    *,
    operator_bundle: BrooklynReferenceBundle,
    brooklyn_bundle: BrooklynReferenceBundle,
) -> dict[str, Any]:
    actuals: list[float] = []
    baseline_predictions: list[float] = []
    brooklyn_predictions: list[float] = []
    operator_predictions: list[float] = []
    for _, row in rows.iterrows():
        feature_vector = {
            name: float(row[name])
            for name in operator_bundle.feature_contract
        }
        baseline_value = float(row["baseline_total_covers"])
        season_name = str(row["season"])
        actual = float(row["target_covers"])
        operator_prediction = brooklyn_delta_uc_prediction(
            season=season_name,
            baseline_uc=baseline_value,
            feature_vector=feature_vector,
            bundle=operator_bundle,
        )
        brooklyn_prediction = brooklyn_delta_uc_prediction(
            season=season_name,
            baseline_uc=baseline_value,
            feature_vector=feature_vector,
            bundle=brooklyn_bundle,
        )
        if operator_prediction is None or brooklyn_prediction is None:
            continue
        actuals.append(actual)
        baseline_predictions.append(baseline_value)
        brooklyn_predictions.append(float(brooklyn_prediction))
        operator_predictions.append(float(operator_prediction))

    baseline_metrics = _metrics("baseline_day_group", actuals, baseline_predictions)
    brooklyn_metrics = _metrics("brooklyn_reference", actuals, brooklyn_predictions)
    operator_metrics = _metrics("operator_history_reference", actuals, operator_predictions)
    operator_selected = (
        operator_metrics.n_rows > 0
        and operator_metrics.mae < brooklyn_metrics.mae
        and operator_metrics.rmse < brooklyn_metrics.rmse
    )
    return {
        "baseline": asdict(baseline_metrics),
        "brooklyn": asdict(brooklyn_metrics),
        "operator_history": asdict(operator_metrics),
        "selection_reason": (
            "operator_history_beats_brooklyn_insample"
            if operator_selected
            else "brooklyn_retained"
        ),
        "selected_for_runtime": operator_selected,
    }


def clear_operator_reference_selection(db: Database, operator_id: str) -> None:
    db.execute(
        """
        UPDATE operator_reference_assets
        SET selected_for_runtime = FALSE,
            last_updated_at = CURRENT_TIMESTAMP
        WHERE operator_id = ?
        """,
        [operator_id],
    )


def load_selected_operator_reference_override(
    db: Database,
    *,
    operator_id: str,
) -> ReferenceRuntimeOverride | None:
    row = db.fetchone(
        """
        SELECT bundle_path, model_name
        FROM operator_reference_assets
        WHERE operator_id = ?
          AND selected_for_runtime = TRUE
          AND training_status = 'completed'
        ORDER BY last_updated_at DESC, created_at DESC
        LIMIT 1
        """,
        [operator_id],
    )
    if row is None or not row[0]:
        return None
    bundle_path = str(row[0])
    if not Path(bundle_path).exists():
        return None
    bundle = _load_bundle_from_path(bundle_path)
    return ReferenceRuntimeOverride(
        bundle=bundle,
        model_name=str(row[1] or DEFAULT_OPERATOR_REFERENCE_MODEL_NAME),
        similarity_override=1.0,
    )


def train_operator_history_reference_asset(
    db: Database,
    *,
    operator_id: str,
    runtime_root: Path,
    cache_root: Path | None = None,
) -> OperatorReferenceTrainingResult:
    upload = load_operator_historical_upload_rows(db, operator_id=operator_id)
    if upload is None:
        clear_operator_reference_selection(db, operator_id)
        return OperatorReferenceTrainingResult(
            status="skipped",
            message="No 12-month history upload was attached, so the default weather reference stays in place.",
            selected_for_runtime=False,
        )

    upload_token, rows = upload
    existing_asset = db.fetchone(
        """
        SELECT asset_id, model_name, bundle_path, benchmark_json, selected_for_runtime, training_status
        FROM operator_reference_assets
        WHERE operator_id = ?
          AND source_upload_token = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [operator_id, upload_token],
    )
    if (
        existing_asset is not None
        and str(existing_asset[5] or "") == "completed"
        and existing_asset[2]
        and Path(str(existing_asset[2])).exists()
    ):
        clear_operator_reference_selection(db, operator_id)
        if bool(existing_asset[4]):
            db.execute(
                """
                UPDATE operator_reference_assets
                SET selected_for_runtime = TRUE,
                    last_updated_at = CURRENT_TIMESTAMP
                WHERE asset_id = ?
                """,
                [str(existing_asset[0])],
            )
        return OperatorReferenceTrainingResult(
            status="completed",
            message=(
                "The local weather reference was already trained from this upload and remains active."
                if bool(existing_asset[4])
                else "Brooklyn remains active because the existing local weather reference did not beat it in-sample."
            ),
            selected_for_runtime=bool(existing_asset[4]),
            model_name=str(existing_asset[1] or DEFAULT_OPERATOR_REFERENCE_MODEL_NAME),
            asset_id=str(existing_asset[0]),
            upload_token=upload_token,
            benchmark=json.loads(str(existing_asset[3] or "{}")) if existing_asset[3] else None,
            bundle_path=str(existing_asset[2]),
        )

    profile = OperatorRepository(db).load_operator_profile(operator_id)
    if profile is None or profile.lat is None or profile.lon is None:
        clear_operator_reference_selection(db, operator_id)
        return OperatorReferenceTrainingResult(
            status="failed",
            message="The restaurant location is incomplete, so the local reference asset cannot be trained yet.",
            selected_for_runtime=False,
            upload_token=upload_token,
        )

    try:
        training_rows = _build_training_frame(
            rows,
            lat=float(profile.lat),
            lon=float(profile.lon),
            timezone_name=profile.timezone or "America/New_York",
            cache_root=cache_root,
        )
    except Exception as exc:
        clear_operator_reference_selection(db, operator_id)
        return OperatorReferenceTrainingResult(
            status="failed",
            message=f"Weather history could not be attached to the uploaded covers yet: {exc}",
            selected_for_runtime=False,
            upload_token=upload_token,
        )

    feature_contract = list(load_brooklyn_reference_bundle().feature_contract)
    if training_rows.empty or len(training_rows) < 40:
        clear_operator_reference_selection(db, operator_id)
        return OperatorReferenceTrainingResult(
            status="failed",
            message="The uploaded history did not produce enough weather-linked dinner rows to train a local reference asset.",
            selected_for_runtime=False,
            upload_token=upload_token,
        )

    try:
        models_by_season = _fit_operator_models(training_rows, feature_contract)
    except Exception as exc:
        clear_operator_reference_selection(db, operator_id)
        return OperatorReferenceTrainingResult(
            status="failed",
            message=f"The local weather reference could not be trained: {exc}",
            selected_for_runtime=False,
            upload_token=upload_token,
        )

    operator_bundle = _build_operator_bundle(
        feature_contract=feature_contract,
        models_by_season=models_by_season,
        rows=training_rows,
        upload_token=upload_token,
        operator_id=operator_id,
    )
    benchmark = _benchmark_reference_bundle(
        training_rows,
        operator_bundle=operator_bundle,
        brooklyn_bundle=load_brooklyn_reference_bundle(),
    )
    selected_for_runtime = bool(benchmark.get("selected_for_runtime"))

    asset_id = f"ref_{uuid.uuid4().hex[:12]}"
    bundle_path = _asset_root(runtime_root) / operator_id / f"{asset_id}.pkl"
    _store_bundle(operator_bundle, bundle_path=bundle_path)
    clear_operator_reference_selection(db, operator_id)
    db.execute(
        """
        INSERT INTO operator_reference_assets (
            asset_id,
            operator_id,
            asset_type,
            model_name,
            bundle_path,
            feature_contract_json,
            benchmark_json,
            training_status,
            selected_for_runtime,
            source_upload_token,
            target_mode,
            last_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            asset_id,
            operator_id,
            "operator_history_reference",
            DEFAULT_OPERATOR_REFERENCE_MODEL_NAME,
            str(bundle_path),
            json.dumps(feature_contract),
            json.dumps(benchmark),
            "completed",
            selected_for_runtime,
            upload_token,
            "total_covers",
        ],
    )
    return OperatorReferenceTrainingResult(
        status="completed",
        message=(
            "The local weather reference beat Brooklyn in-sample and is now active."
            if selected_for_runtime
            else "Brooklyn remains active because the local weather reference did not beat it in-sample."
        ),
        selected_for_runtime=selected_for_runtime,
        model_name=DEFAULT_OPERATOR_REFERENCE_MODEL_NAME,
        asset_id=asset_id,
        upload_token=upload_token,
        benchmark=benchmark,
        bundle_path=str(bundle_path),
    )
