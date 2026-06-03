from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import json
import uuid
from typing import Any

from stormready_v3.ai.contracts import AgentModelProvider, ExternalSourceGovernanceItem
from stormready_v3.config.settings import EXTERNAL_SOURCE_AI_GOVERNANCE_MODE
from stormready_v3.domain.enums import RefreshReason
from stormready_v3.domain.models import LocationContextProfile, OperatorProfile
from stormready_v3.external_intelligence.source_packs import (
    SourcePackSeed,
    build_broad_discovery_seeds,
    build_permanent_source_seeds,
)
from stormready_v3.storage.db import Database


EXTERNAL_SOURCE_CATEGORIES = (
    "traffic_access",
    "events_venues",
    "incidents_safety",
    "neighborhood_demand_proxy",
    "civic_campus",
    "tourism_hospitality",
)


@dataclass(slots=True)
class ExternalCatalogEntry:
    operator_id: str
    source_name: str
    source_bucket: str
    scan_scope: str
    source_category: str
    discovery_mode: str
    source_kind: str
    source_class: str
    trust_class: str
    cadence_hint: str
    status: str
    entity_label: str
    geo_scope: str
    endpoint_hint: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def source_key(self) -> str:
        return ":".join(
            [
                self.operator_id,
                self.source_bucket,
                self.scan_scope,
                self.source_category,
                self.source_name,
            ]
        )


class ExternalSourceCatalogService:
    def __init__(
        self,
        db: Database,
        *,
        provider: AgentModelProvider | None = None,
        governance_mode: str | None = None,
    ) -> None:
        self.db = db
        self.provider = provider
        self.governance_mode = (governance_mode or EXTERNAL_SOURCE_AI_GOVERNANCE_MODE).lower()

    AI_GOVERNANCE_BATCH_SIZE = 2

    @staticmethod
    def _db_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def run_refresh_discovery(
        self,
        *,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        run_date: date,
        refresh_reason: RefreshReason,
        refresh_window: str | None,
        scanned_at: datetime,
    ) -> dict[str, Any]:
        curated_entries = self._curated_seed_entries(profile, location_context)
        broad_entries = self._broad_discovery_entries(profile, location_context, refresh_reason=refresh_reason)
        curated_seed_count = self._upsert_entries(curated_entries, scanned_at=scanned_at)
        broad_discovery_count = self._upsert_entries(broad_entries, scanned_at=scanned_at)
        governance_result = self._govern_catalog_entries(
            profile=profile,
            location_context=location_context,
            refresh_reason=refresh_reason,
            refresh_window=refresh_window,
            scanned_at=scanned_at,
            curated_entries=curated_entries,
            broad_entries=broad_entries,
        )

        summary = self.current_summary(profile.operator_id)
        summary.update(
            {
                "curated_seed_count": curated_seed_count,
                "broad_discovery_count": broad_discovery_count,
                "categories_supported": list(EXTERNAL_SOURCE_CATEGORIES),
                "scan_mode": "continuous_refresh_discovery",
                "governance_applied": bool(governance_result.get("governance_applied")),
                "governance_mode": str(governance_result.get("governance_mode") or "policy"),
            }
        )
        self._log_scan_run(
            operator_id=profile.operator_id,
            run_date=run_date,
            refresh_reason=refresh_reason,
            refresh_window=refresh_window,
            summary=summary,
            scanned_at=scanned_at,
        )
        return summary

    def current_summary(self, operator_id: str) -> dict[str, Any]:
        rows = self.db.fetchall(
            """
            SELECT source_bucket, status, source_category, source_name, entity_label,
                   recommended_action, priority_score, governance_confidence,
                   governance_source, governance_provider, governance_fallback_reason
            FROM external_source_catalog
            WHERE operator_id = ?
            ORDER BY source_bucket, source_category, source_name
            """,
            [operator_id],
        )
        curated_sources: list[dict[str, str]] = []
        discovered_sources: list[dict[str, str]] = []
        category_counts: dict[str, int] = {}
        governance_source_counts: dict[str, int] = {}
        for (
            source_bucket,
            status,
            source_category,
            source_name,
            entity_label,
            recommended_action,
            priority_score,
            governance_confidence,
            governance_source,
            governance_provider,
            governance_fallback_reason,
        ) in rows:
            category_counts[str(source_category)] = category_counts.get(str(source_category), 0) + 1
            governance_key = str(governance_source or "unclassified")
            governance_source_counts[governance_key] = governance_source_counts.get(governance_key, 0) + 1
            row = {
                "source_bucket": str(source_bucket),
                "status": str(status),
                "source_category": str(source_category),
                "source_name": str(source_name),
                "entity_label": str(entity_label or source_name),
                "recommended_action": str(recommended_action) if recommended_action is not None else None,
                "priority_score": float(priority_score) if priority_score is not None else None,
                "governance_confidence": str(governance_confidence) if governance_confidence is not None else None,
                "governance_source": str(governance_source) if governance_source is not None else None,
                "governance_provider": str(governance_provider) if governance_provider is not None else None,
                "governance_fallback_reason": (
                    str(governance_fallback_reason) if governance_fallback_reason is not None else None
                ),
            }
            if str(status) == "curated":
                curated_sources.append(row)
            else:
                discovered_sources.append(row)
        return {
            "active_curated_count": len(curated_sources),
            "active_discovered_count": len(discovered_sources),
            "category_counts": category_counts,
            "governance_source_counts": governance_source_counts,
            "curated_sources": curated_sources[:8],
            "discovered_sources": discovered_sources[:8],
        }

    def _upsert_entries(self, entries: list[ExternalCatalogEntry], *, scanned_at: datetime) -> int:
        deduped_entries = self._dedupe_entries(entries)
        timestamp = self._db_timestamp(scanned_at)
        for entry in deduped_entries:
            self.db.execute(
                """
                INSERT INTO external_source_catalog (
                    source_key, operator_id, source_name, source_bucket, scan_scope, source_category,
                    source_kind, source_class, discovery_mode, trust_class, cadence_hint, status,
                    endpoint_hint, entity_label, geo_scope, metadata_json, discovered_at, first_activated_at,
                    last_seen_at, last_scanned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE
                SET source_name = EXCLUDED.source_name,
                    source_bucket = EXCLUDED.source_bucket,
                    scan_scope = EXCLUDED.scan_scope,
                    source_category = EXCLUDED.source_category,
                    source_kind = EXCLUDED.source_kind,
                    source_class = EXCLUDED.source_class,
                    discovery_mode = EXCLUDED.discovery_mode,
                    trust_class = EXCLUDED.trust_class,
                    cadence_hint = EXCLUDED.cadence_hint,
                    status = CASE
                        WHEN external_source_catalog.status = 'retired' THEN external_source_catalog.status
                        WHEN EXCLUDED.status = 'curated' THEN 'curated'
                        ELSE EXCLUDED.status
                    END,
                    endpoint_hint = EXCLUDED.endpoint_hint,
                    entity_label = EXCLUDED.entity_label,
                    geo_scope = EXCLUDED.geo_scope,
                    metadata_json = EXCLUDED.metadata_json,
                    recommended_category = COALESCE(external_source_catalog.recommended_category, EXCLUDED.recommended_category),
                    recommended_action = COALESCE(external_source_catalog.recommended_action, EXCLUDED.recommended_action),
                    priority_score = COALESCE(external_source_catalog.priority_score, EXCLUDED.priority_score),
                    governance_confidence = COALESCE(external_source_catalog.governance_confidence, EXCLUDED.governance_confidence),
                    governance_notes_json = COALESCE(external_source_catalog.governance_notes_json, EXCLUDED.governance_notes_json),
                    last_governed_at = COALESCE(external_source_catalog.last_governed_at, EXCLUDED.last_governed_at),
                    first_activated_at = COALESCE(external_source_catalog.first_activated_at, EXCLUDED.first_activated_at),
                    last_seen_at = EXCLUDED.last_seen_at,
                    last_scanned_at = EXCLUDED.last_scanned_at
                """,
                [
                    entry.source_key,
                    entry.operator_id,
                    entry.source_name,
                    entry.source_bucket,
                    entry.scan_scope,
                    entry.source_category,
                    entry.source_kind,
                    entry.source_class,
                    entry.discovery_mode,
                    entry.trust_class,
                    entry.cadence_hint,
                    entry.status,
                    entry.endpoint_hint,
                    entry.entity_label,
                    entry.geo_scope,
                    json.dumps(entry.metadata or {}),
                    timestamp,
                    timestamp if entry.status == "curated" else None,
                    timestamp,
                    timestamp,
                ],
            )
        return len(deduped_entries)

    @staticmethod
    def _dedupe_entries(entries: list[ExternalCatalogEntry]) -> list[ExternalCatalogEntry]:
        by_source_key: dict[str, ExternalCatalogEntry] = {}
        ordered_keys: list[str] = []
        for entry in entries:
            source_key = str(entry.source_key)
            if source_key not in by_source_key:
                ordered_keys.append(source_key)
            by_source_key[source_key] = entry
        return [by_source_key[source_key] for source_key in ordered_keys]

    def _govern_catalog_entries(
        self,
        *,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        refresh_reason: RefreshReason,
        refresh_window: str | None,
        scanned_at: datetime,
        curated_entries: list[ExternalCatalogEntry],
        broad_entries: list[ExternalCatalogEntry],
    ) -> dict[str, Any]:
        entries = [*curated_entries, *broad_entries]
        heuristics = self._heuristic_governance(entries, location_context=location_context)
        provider_label = self._provider_label()
        used_provider = False
        if self._should_use_ai_governance(refresh_reason=refresh_reason, refresh_window=refresh_window):
            provider_entries = broad_entries[:4]
            ai_result = self._provider_governance(
                profile=profile,
                location_context=location_context,
                entries=provider_entries,
            )
            if ai_result["advice"]:
                used_provider = True
                for source_name, advice in ai_result["advice"].items():
                    if source_name not in heuristics:
                        continue
                    merged = heuristics[source_name]
                    if advice.recommended_category in EXTERNAL_SOURCE_CATEGORIES:
                        merged["recommended_category"] = advice.recommended_category
                    if advice.recommended_action in {"keep_curated", "promote_candidate", "monitor", "ignore"}:
                        merged["recommended_action"] = advice.recommended_action
                    if advice.priority_score is not None:
                        merged["priority_score"] = max(0.0, min(1.0, float(advice.priority_score)))
                    if advice.confidence:
                        merged["governance_confidence"] = advice.confidence
                    if advice.cadence_hint:
                        merged["cadence_hint"] = advice.cadence_hint
                    if advice.notes:
                        merged["governance_notes"] = list(dict.fromkeys([*merged["governance_notes"], *advice.notes]))
                    merged["governance_source"] = ai_result["source_by_name"].get(source_name, "policy_default")
                    merged["governance_provider"] = provider_label
                    merged["governance_fallback_reason"] = ai_result["fallback_by_name"].get(source_name)
            for entry in provider_entries:
                merged = heuristics.get(entry.source_name)
                if merged is None:
                    continue
                merged.setdefault("governance_provider", provider_label)
                merged.setdefault("governance_source", "policy_default")
                merged.setdefault("governance_fallback_reason", ai_result["fallback_by_name"].get(entry.source_name))
        else:
            for entry in entries:
                merged = heuristics.get(entry.source_name)
                if merged is None:
                    continue
                merged["governance_source"] = "policy_default"
                merged["governance_provider"] = provider_label
                merged["governance_fallback_reason"] = "governance_not_eligible"

        self._persist_governance(entries=entries, governance=heuristics, governed_at=scanned_at)
        return {
            "governance_applied": True,
            "governance_mode": "ai" if used_provider else "policy",
        }

    def _should_use_ai_governance(self, *, refresh_reason: RefreshReason, refresh_window: str | None) -> bool:
        if self.governance_mode in {"disabled", "off", "false", "0"}:
            return False
        if self.provider is None or not self.provider.is_available():
            return False
        if self.governance_mode in {"enabled", "always", "true", "1"}:
            return True
        if refresh_reason is RefreshReason.SCHEDULED and refresh_window == "morning":
            return True
        if refresh_reason is RefreshReason.OPERATOR_REQUESTED:
            return True
        return False

    def _provider_governance(
        self,
        *,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        entries: list[ExternalCatalogEntry],
    ) -> dict[str, Any]:
        if self.provider is None or not self.provider.is_available():
            return {
                "advice": {},
                "source_by_name": {},
                "fallback_by_name": {entry.source_name: "provider_unavailable" for entry in entries},
            }
        operator_context: dict[str, Any] = {
            "operator_id": profile.operator_id,
            "city": profile.city,
            "address": profile.canonical_address,
            "neighborhood_archetype": location_context.neighborhood_archetype.value,
            "transit_relevance": location_context.transit_relevance,
            "venue_relevance": location_context.venue_relevance,
            "hotel_travel_relevance": location_context.hotel_travel_relevance,
            "demand_mix": profile.demand_mix.value,
        }
        # Enrich with source reliability history so AI can weight recommendations
        reliability = self._load_source_reliability_for_governance(profile.operator_id)
        if reliability:
            operator_context["source_reliability_history"] = reliability
        advice_by_source: dict[str, ExternalSourceGovernanceItem] = {}
        source_by_name: dict[str, str] = {}
        fallback_by_name: dict[str, str | None] = {entry.source_name: None for entry in entries}
        batch_size = max(1, int(self.AI_GOVERNANCE_BATCH_SIZE))
        for start in range(0, len(entries), batch_size):
            batch = entries[start : start + batch_size]
            response = self.provider.external_source_governance(
                operator_context=operator_context,
                source_candidates=[self._entry_candidate_payload(entry) for entry in batch],
            )
            if not response:
                fallback_reason = self._provider_failure_reason(default="provider_empty_batch")
                for entry in batch:
                    single_response = self.provider.external_source_governance(
                        operator_context=operator_context,
                        source_candidates=[self._entry_candidate_payload(entry)],
                    )
                    if not single_response:
                        fallback_by_name[entry.source_name] = self._provider_failure_reason(default=fallback_reason)
                        continue
                    for item in single_response:
                        advice_by_source[item.source_name] = item
                        source_by_name[item.source_name] = "ai_singleton_retry"
                        fallback_by_name[item.source_name] = fallback_reason
                continue
            matched_sources = set()
            for item in response:
                advice_by_source[item.source_name] = item
                source_by_name[item.source_name] = "ai_batch"
                fallback_by_name[item.source_name] = None
                matched_sources.add(item.source_name)
            for entry in batch:
                if entry.source_name in matched_sources:
                    continue
                single_response = self.provider.external_source_governance(
                    operator_context=operator_context,
                    source_candidates=[self._entry_candidate_payload(entry)],
                )
                if not single_response:
                    fallback_by_name[entry.source_name] = self._provider_failure_reason(default="batch_partial_missing")
                    continue
                for item in single_response:
                    advice_by_source[item.source_name] = item
                    source_by_name[item.source_name] = "ai_singleton_retry"
                    fallback_by_name[item.source_name] = "batch_partial_missing"
        return {
            "advice": advice_by_source,
            "source_by_name": source_by_name,
            "fallback_by_name": fallback_by_name,
        }

    @staticmethod
    def _entry_candidate_payload(entry: ExternalCatalogEntry) -> dict[str, Any]:
        return {
            "source_name": entry.source_name,
            "source_bucket": entry.source_bucket,
            "source_category": entry.source_category,
            "discovery_mode": entry.discovery_mode,
            "source_kind": entry.source_kind,
            "trust_class": entry.trust_class,
            "cadence_hint": entry.cadence_hint,
            "entity_label": entry.entity_label,
            "geo_scope": entry.geo_scope,
            "endpoint_hint": entry.endpoint_hint,
            "metadata": entry.metadata or {},
        }

    def _heuristic_governance(
        self,
        entries: list[ExternalCatalogEntry],
        *,
        location_context: LocationContextProfile,
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for entry in entries:
            priority = self._heuristic_priority(entry, location_context=location_context)
            action = self._heuristic_action(entry, priority=priority)
            confidence = "high" if entry.status == "curated" else ("medium" if action == "monitor" else "low")
            results[entry.source_name] = {
                "recommended_category": entry.source_category,
                "recommended_action": action,
                "priority_score": round(priority, 3),
                "governance_confidence": confidence,
                "cadence_hint": entry.cadence_hint,
                "governance_source": "policy_default",
                "governance_provider": self._provider_label(),
                "governance_fallback_reason": None,
                "governance_notes": [
                    f"{entry.source_bucket}::{entry.source_category}",
                    f"geo_scope::{entry.geo_scope}",
                ],
            }
        return results

    def _heuristic_priority(
        self,
        entry: ExternalCatalogEntry,
        *,
        location_context: LocationContextProfile,
    ) -> float:
        base_priority = 0.65 if entry.status == "curated" else 0.3
        relevance_score = self._category_relevance_score(entry.source_category, location_context=location_context)
        scope_bonus = 0.08 if entry.geo_scope in {"micro", "micro_hyperlocal", "hyperlocal", "corridor"} else 0.0
        trust_bonus = 0.08 if entry.trust_class in {"official", "official_public", "operator_confirmed"} else 0.0
        proxy_penalty = -0.08 if entry.source_bucket == "broad_proxy" else 0.0
        return max(0.0, min(0.95, base_priority + relevance_score + scope_bonus + trust_bonus + proxy_penalty))

    @staticmethod
    def _category_relevance_score(
        source_category: str,
        *,
        location_context: LocationContextProfile,
    ) -> float:
        if source_category == "traffic_access":
            return 0.26 if location_context.transit_relevance else 0.0
        if source_category == "events_venues":
            return 0.26 if location_context.venue_relevance else 0.0
        if source_category == "tourism_hospitality":
            return 0.22 if location_context.hotel_travel_relevance else 0.0
        if source_category == "incidents_safety":
            if location_context.transit_relevance or location_context.venue_relevance:
                return 0.18
            return 0.08
        if source_category == "neighborhood_demand_proxy":
            return 0.14
        if source_category == "civic_campus":
            return 0.12
        return 0.0

    @staticmethod
    def _heuristic_action(entry: ExternalCatalogEntry, *, priority: float) -> str:
        if entry.status == "curated":
            return "keep_curated"
        if priority >= 0.42:
            return "monitor"
        return "ignore"

    def _persist_governance(
        self,
        *,
        entries: list[ExternalCatalogEntry],
        governance: dict[str, dict[str, Any]],
        governed_at: datetime,
    ) -> None:
        timestamp = self._db_timestamp(governed_at)
        for entry in entries:
            advice = governance.get(entry.source_name)
            if advice is None:
                continue
            self.db.execute(
                """
                UPDATE external_source_catalog
                SET recommended_category = ?,
                    recommended_action = ?,
                    priority_score = ?,
                    governance_confidence = ?,
                    governance_source = ?,
                    governance_provider = ?,
                    governance_fallback_reason = ?,
                    governance_notes_json = ?,
                    cadence_hint = ?,
                    last_governed_at = ?
                WHERE source_key = ?
                """,
                [
                    advice.get("recommended_category"),
                    advice.get("recommended_action"),
                    advice.get("priority_score"),
                    advice.get("governance_confidence"),
                    advice.get("governance_source"),
                    advice.get("governance_provider"),
                    advice.get("governance_fallback_reason"),
                    json.dumps(advice.get("governance_notes") or []),
                    advice.get("cadence_hint"),
                    timestamp,
                    entry.source_key,
                ],
            )

    def _load_source_reliability_for_governance(self, operator_id: str) -> list[dict[str, Any]]:
        """Load source reliability summary for the AI governance prompt."""
        rows = self.db.fetchall(
            """
            SELECT source_name, signal_type, historical_usefulness_score, trust_class, sample_size
            FROM source_reliability_state
            WHERE operator_id = ? AND status = 'active'
            ORDER BY historical_usefulness_score DESC
            LIMIT 10
            """,
            [operator_id],
        )
        return [
            {
                "source": row[0],
                "signal": row[1],
                "usefulness": round(float(row[2] or 0), 2),
                "trust": row[3],
                "samples": int(row[4] or 0),
            }
            for row in rows
        ]

    def _provider_label(self) -> str | None:
        if self.provider is None:
            return None
        configured_provider = getattr(self.provider, "configured_provider", None)
        if callable(configured_provider):
            try:
                return configured_provider()
            except Exception:
                return "unknown_provider"
        return self.provider.__class__.__name__

    def _provider_failure_reason(self, *, default: str) -> str:
        if self.provider is None:
            return default
        last_failure_reason = getattr(self.provider, "last_failure_reason", None)
        if callable(last_failure_reason):
            try:
                return str(last_failure_reason() or default)
            except Exception:
                return default
        return default

    def _log_scan_run(
        self,
        *,
        operator_id: str,
        run_date: date,
        refresh_reason: RefreshReason,
        refresh_window: str | None,
        summary: dict[str, Any],
        scanned_at: datetime,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO external_scan_run_log (
                scan_run_id, operator_id, run_date, refresh_reason, refresh_window, scan_mode,
                curated_seed_count, broad_discovery_count, active_curated_count, active_discovered_count,
                summary_json, scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"scan_{uuid.uuid4().hex[:12]}",
                operator_id,
                run_date,
                refresh_reason.value,
                refresh_window,
                summary.get("scan_mode", "continuous_refresh_discovery"),
                int(summary.get("curated_seed_count", 0) or 0),
                int(summary.get("broad_discovery_count", 0) or 0),
                int(summary.get("active_curated_count", 0) or 0),
                int(summary.get("active_discovered_count", 0) or 0),
                json.dumps(summary),
                self._db_timestamp(scanned_at),
            ],
        )

    @staticmethod
    def _entry_from_seed(operator_id: str, seed: SourcePackSeed) -> ExternalCatalogEntry:
        return ExternalCatalogEntry(
            operator_id=operator_id,
            source_name=seed.source_name,
            source_bucket=seed.source_bucket,
            scan_scope=seed.scan_scope,
            source_category=seed.source_category,
            discovery_mode=seed.discovery_mode,
            source_kind=seed.source_kind,
            source_class=seed.source_class,
            trust_class=seed.trust_class,
            cadence_hint=seed.cadence_hint,
            status=seed.status,
            entity_label=seed.entity_label,
            geo_scope=seed.geo_scope,
            endpoint_hint=seed.endpoint_hint,
            metadata=seed.metadata,
        )

    def _curated_seed_entries(
        self,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
    ) -> list[ExternalCatalogEntry]:
        return [
            self._entry_from_seed(profile.operator_id, seed)
            for seed in build_permanent_source_seeds(profile, location_context)
        ]

    def _broad_discovery_entries(
        self,
        profile: OperatorProfile,
        location_context: LocationContextProfile,
        *,
        refresh_reason: RefreshReason,
    ) -> list[ExternalCatalogEntry]:
        return [
            self._entry_from_seed(profile.operator_id, seed)
            for seed in build_broad_discovery_seeds(
                profile,
                location_context,
                refresh_reason=refresh_reason.value,
            )
        ]
