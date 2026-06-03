"""Deterministic setup/enrichment digests for Agent C.

Operations digests are produced by retriever agents after forecast/actual/note
events. Setup and enrichment need a smaller cache of profile/readiness context
before the normal forecast event loop exists, so this module writes bounded
CurrentStateDigest and TemporalContextDigest rows directly from setup state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from stormready_v3.agents.digests import CurrentStateDigest, TemporalContextDigest
from stormready_v3.setup.readiness import summarize_setup_readiness
from stormready_v3.storage.db import Database
from stormready_v3.storage.repositories import OperatorContextDigestRepository, OperatorRepository


def ensure_setup_context_digests(
    db: Database,
    *,
    operator_id: str,
    reference_date: date,
    force: bool = False,
) -> None:
    """Write setup/enrichment context digests if they are missing or forced."""

    digest_repo = OperatorContextDigestRepository(db)
    if not force:
        current = digest_repo.fetch_latest(operator_id=operator_id, kind="current_state")
        temporal = digest_repo.fetch_latest(operator_id=operator_id, kind="temporal")
        current_phase = (current or {}).get("payload", {}).get("phase")
        if current is not None and temporal is not None and current_phase in {"setup", "enrichment"}:
            return

    repo = OperatorRepository(db)
    profile = repo.load_operator_profile(operator_id)
    if profile is None:
        return

    now = datetime.now(UTC)
    baselines = _load_baselines(db, operator_id)
    has_baselines = any(value > 0 for value in baselines.values())
    summary = summarize_setup_readiness(profile, primary_window_has_baseline=has_baselines)
    phase = _phase_for_setup_context(db, operator_id, summary.forecast_ready)
    if phase == "operations":
        return

    location = repo.load_location_context(operator_id)
    payload_seed = {
        "operator_id": operator_id,
        "reference_date": reference_date.isoformat(),
        "phase": phase,
        "profile": {
            "restaurant_name": profile.restaurant_name,
            "canonical_address": profile.canonical_address,
            "city": profile.city,
            "timezone": profile.timezone,
            "neighborhood_type": profile.neighborhood_type.value,
            "demand_mix": profile.demand_mix.value,
            "patio_enabled": profile.patio_enabled,
            "patio_seat_capacity": profile.patio_seat_capacity,
            "onboarding_state": profile.onboarding_state.value,
        },
        "baselines": baselines,
        "forecast_ready": summary.forecast_ready,
        "improvements": list(summary.improvements or []),
    }
    source_hash = _hash_payload(payload_seed)

    current_digest = CurrentStateDigest(
        produced_at=now,
        source_hash=source_hash,
        reference_date=reference_date,
        phase=phase,
        identity={
            "operator_id": operator_id,
            "venue_name": profile.restaurant_name,
            "city": profile.city,
            "timezone": profile.timezone,
            "canonical_address": profile.canonical_address,
            "setup": {
                "onboarding_state": summary.onboarding_state.value,
                "prediction_case": summary.prediction_case.value,
                "forecast_ready": summary.forecast_ready,
                "has_baselines": has_baselines,
                "baselines": baselines,
                "neighborhood_type": profile.neighborhood_type.value,
                "demand_mix": profile.demand_mix.value,
                "patio_enabled": profile.patio_enabled,
                "patio_seat_capacity": profile.patio_seat_capacity,
                "location_context": _location_context_payload(location),
            },
        },
        headline_forecast=None,
        near_horizon=[],
        pending_action=_pending_setup_action(phase=phase, summary=summary, has_baselines=has_baselines),
        current_uncertainty=_setup_uncertainty(phase=phase, summary=summary),
        active_signals_summary=_setup_signal_summary(profile=profile, location=location),
        missing_actuals=[],
        disclaimers=_setup_disclaimers(phase=phase),
    )
    temporal_digest = TemporalContextDigest(
        produced_at=now,
        source_hash=source_hash,
        conversation_state="cold_start",
        recent_misses=[],
        active_hypotheses=[],
        recent_patterns=[],
        operator_facts=_operator_setup_facts(profile=profile, baselines=baselines, has_baselines=has_baselines),
        learning_maturity={
            "samples": _actual_count(db, operator_id),
            "phase": phase,
            "forecast_ready": summary.forecast_ready,
            "cascades_live": [],
            "demoted_sources": [],
        },
        open_questions=_setup_open_questions(phase=phase, summary=summary, has_baselines=has_baselines),
        disclaimers=_setup_disclaimers(phase=phase),
    )

    digest_repo.insert_digest(
        operator_id=operator_id,
        kind="current_state",
        produced_at=now,
        source_hash=source_hash,
        payload_json=current_digest.to_json(),
        agent_run_id=None,
    )
    digest_repo.insert_digest(
        operator_id=operator_id,
        kind="temporal",
        produced_at=now,
        source_hash=source_hash,
        payload_json=temporal_digest.to_json(),
        agent_run_id=None,
    )


def _phase_for_setup_context(db: Database, operator_id: str, forecast_ready: bool) -> str:
    if not forecast_ready:
        return "setup"
    row = db.fetchone(
        "SELECT COUNT(*) FROM published_forecast_state WHERE operator_id = ?",
        [operator_id],
    )
    has_forecasts = row is not None and int(row[0] or 0) > 0
    return "operations" if has_forecasts else "enrichment"


def _load_baselines(db: Database, operator_id: str) -> dict[str, int]:
    rows = db.fetchall(
        """
        SELECT day_group, baseline_total_covers
        FROM operator_weekly_baselines
        WHERE operator_id = ?
          AND service_window = 'dinner'
        """,
        [operator_id],
    )
    return {
        str(row[0]): int(row[1] or 0)
        for row in rows
        if row[0] is not None
    }


def _pending_setup_action(*, phase: str, summary: Any, has_baselines: bool) -> dict[str, Any] | None:
    if phase == "setup":
        if not getattr(summary, "has_address", False):
            return {
                "kind": "complete_profile",
                "prompt": "Add the restaurant name and street address.",
                "urgency": "high",
            }
        if not has_baselines:
            return {
                "kind": "add_baselines",
                "prompt": "Add typical dinner cover counts for Mon-Thu, Friday, Saturday, and Sunday.",
                "urgency": "high",
            }
        return {
            "kind": "finish_setup",
            "prompt": "Finish the remaining setup details.",
            "urgency": "medium",
        }
    return {
        "kind": "optional_enrichment",
        "prompt": "Add optional history, connection, or neighborhood context, or start using forecasts.",
        "urgency": "low",
    }


def _setup_uncertainty(*, phase: str, summary: Any) -> str | None:
    if phase == "setup":
        improvements = list(getattr(summary, "improvements", []) or [])
        if improvements:
            return str(improvements[0])
        return "Setup is still incomplete."
    return "Forecasts are ready; optional enrichment can improve confidence."


def _setup_signal_summary(*, profile: Any, location: Any) -> list[str]:
    signals: list[str] = []
    signals.append(f"Demand mix is {profile.demand_mix.value}.")
    signals.append(f"Neighborhood type is {profile.neighborhood_type.value}.")
    if profile.patio_enabled:
        patio = "Patio is enabled"
        if profile.patio_seat_capacity:
            patio += f" with {profile.patio_seat_capacity} seats"
        signals.append(patio + ".")
    if location is not None:
        if location.transit_relevance:
            signals.append("Transit nearby is marked relevant.")
        if location.venue_relevance:
            signals.append("Nearby venues are marked relevant.")
        if location.hotel_travel_relevance:
            signals.append("Hotel or travel demand is marked relevant.")
    return signals[:5]


def _operator_setup_facts(*, profile: Any, baselines: dict[str, int], has_baselines: bool) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = [
        {"key": "restaurant_name", "value": profile.restaurant_name, "confidence": "high"},
    ]
    if profile.canonical_address:
        facts.append({"key": "canonical_address", "value": profile.canonical_address, "confidence": "high"})
    facts.append({"key": "demand_mix", "value": profile.demand_mix.value, "confidence": "medium"})
    facts.append({"key": "neighborhood_type", "value": profile.neighborhood_type.value, "confidence": "medium"})
    if has_baselines:
        baseline_text = ", ".join(f"{key}: {value}" for key, value in sorted(baselines.items()))
        facts.append({"key": "weekly_baselines", "value": baseline_text, "confidence": "high"})
    if profile.patio_enabled:
        facts.append(
            {
                "key": "patio",
                "value": f"enabled; seats={profile.patio_seat_capacity or 'unknown'}",
                "confidence": "medium",
            }
        )
    return facts[:6]


def _setup_open_questions(*, phase: str, summary: Any, has_baselines: bool) -> list[dict[str, Any]]:
    pending = _pending_setup_action(phase=phase, summary=summary, has_baselines=has_baselines)
    if pending is None:
        return []
    return [
        {
            "agenda_key": f"setup::{pending['kind']}",
            "prompt": pending["prompt"],
            "ready_to_ask": True,
        }
    ]


def _setup_disclaimers(*, phase: str) -> list[str]:
    if phase == "setup":
        return ["Forecasts are not ready until the required setup details are complete."]
    return ["Forecasts are ready; optional enrichment can improve future accuracy."]


def _location_context_payload(location: Any) -> dict[str, Any]:
    if location is None:
        return {}
    return {
        "transit_relevance": bool(location.transit_relevance),
        "venue_relevance": bool(location.venue_relevance),
        "hotel_travel_relevance": bool(location.hotel_travel_relevance),
    }


def _actual_count(db: Database, operator_id: str) -> int:
    row = db.fetchone("SELECT COUNT(*) FROM operator_actuals WHERE operator_id = ?", [operator_id])
    return int(row[0] or 0) if row is not None else 0


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


__all__ = ["ensure_setup_context_digests"]
