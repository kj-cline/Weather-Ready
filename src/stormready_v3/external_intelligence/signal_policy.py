from __future__ import annotations

from dataclasses import dataclass

from stormready_v3.domain.models import NormalizedSignal


@dataclass(slots=True)
class DependencyGroupCorroboration:
    dependency_group: str
    source_names: list[str]
    source_buckets: list[str]
    source_categories: list[str]
    signal_count: int
    corroborated: bool
    broad_numeric_eligible: bool


def summarize_dependency_group_corroboration(
    signals: list[NormalizedSignal],
) -> dict[str, DependencyGroupCorroboration]:
    grouped: dict[str, dict[str, object]] = {}
    for signal in signals:
        if signal.dependency_group == "weather":
            continue
        row = grouped.setdefault(
            signal.dependency_group,
            {
                "source_names": set(),
                "source_buckets": set(),
                "source_categories": set(),
                "signal_count": 0,
                "has_broad_numeric": False,
                "has_non_broad_numeric": False,
            },
        )
        row["signal_count"] = int(row["signal_count"]) + 1
        row["source_names"].add(signal.source_name)
        row["source_buckets"].add(signal.source_bucket)
        category = signal.details.get("source_category")
        if category:
            row["source_categories"].add(str(category))
        if signal.role.value == "numeric_mover":
            if signal.source_bucket == "broad_proxy":
                row["has_broad_numeric"] = True
            else:
                row["has_non_broad_numeric"] = True

    results: dict[str, DependencyGroupCorroboration] = {}
    for dependency_group, row in grouped.items():
        source_names = sorted(str(name) for name in row["source_names"])
        source_buckets = sorted(str(bucket) for bucket in row["source_buckets"])
        corroborated = len(source_names) >= 2
        broad_numeric_eligible = bool(row["has_non_broad_numeric"]) or corroborated
        results[dependency_group] = DependencyGroupCorroboration(
            dependency_group=dependency_group,
            source_names=source_names,
            source_buckets=source_buckets,
            source_categories=sorted(str(category) for category in row["source_categories"]),
            signal_count=int(row["signal_count"]),
            corroborated=corroborated,
            broad_numeric_eligible=broad_numeric_eligible,
        )
    return results

