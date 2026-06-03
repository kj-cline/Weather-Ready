from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import io
import json
import re
import uuid
from typing import Any

from stormready_v3.ai.contracts import AgentModelProvider
from stormready_v3.domain.enums import ServiceState, ServiceWindow
from stormready_v3.mvp_scope import is_runtime_window_supported
from stormready_v3.prediction.engine import day_group_for_date
from stormready_v3.reference.brooklyn import season_from_service_date
from stormready_v3.storage.db import Database


_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "service_date": (
        "service_date",
        "date",
        "business_date",
        "shift_date",
        "service_day",
        "day",
    ),
    "realized_total_covers": (
        "realized_total_covers",
        "total_covers",
        "covers",
        "guest_count",
        "guestcount",
        "guests",
        "total_guests",
    ),
    "realized_reserved_covers": (
        "realized_reserved_covers",
        "reserved_covers",
        "reservation_covers",
        "seated_reservation_covers",
    ),
    "outside_covers": (
        "outside_covers",
        "patio_covers",
        "outdoor_covers",
    ),
    "service_window": (
        "service_window",
        "shift",
        "meal_period",
    ),
    "service_state": (
        "service_state",
        "operating_state",
        "shift_state",
        "status",
    ),
}


def _canonical_header(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())).strip("_")


def _parse_csv_rows(content: str) -> list[dict[str, Any]]:
    sample = "\n".join(content.splitlines()[:10]) or content
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    return [dict(row) for row in reader if row]


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    raw = str(value).strip()
    if not raw:
        return None
    for candidate in (raw, raw[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _maybe_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _parse_service_window(value: Any, *, default_window: ServiceWindow) -> ServiceWindow:
    if isinstance(value, ServiceWindow):
        return value
    raw = _canonical_header(value)
    for window in ServiceWindow:
        if raw == _canonical_header(window.value):
            return window
    return default_window


def _parse_service_state(value: Any) -> ServiceState:
    if isinstance(value, ServiceState):
        return value
    raw = _canonical_header(value)
    for state in ServiceState:
        if raw == _canonical_header(state.value):
            return state
    return ServiceState.NORMAL


def _detect_mapping(headers: list[str]) -> dict[str, str]:
    header_map = {_canonical_header(header): header for header in headers}
    mapping: dict[str, str] = {}
    for canonical_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            actual = header_map.get(_canonical_header(alias))
            if actual is not None:
                mapping[canonical_name] = actual
                break
    return mapping


@dataclass(slots=True)
class HistoricalUploadRow:
    service_date: str
    service_window: str
    realized_total_covers: int
    realized_reserved_covers: int | None = None
    outside_covers: int | None = None
    service_state: str = ServiceState.NORMAL.value


@dataclass(slots=True)
class HistoricalUploadReview:
    accepted: bool
    summary: str
    upload_token: str
    file_name: str
    usable_rows: int
    skipped_rows: int
    normal_service_rows: int
    distinct_months: int
    seasons_covered: list[str]
    baseline_values: dict[str, int] = field(default_factory=dict)
    format_confidence: str = "medium"
    warnings: list[str] = field(default_factory=list)
    ai_summary: str | None = None
    ai_warnings: list[str] = field(default_factory=list)
    requirement_failures: list[str] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)
    first_service_date: str | None = None
    last_service_date: str | None = None


def _serialize_rows(rows: list[HistoricalUploadRow]) -> str:
    return json.dumps([asdict(row) for row in rows], default=str)


def _deserialize_rows(blob: str) -> list[HistoricalUploadRow]:
    parsed = json.loads(blob or "[]")
    rows: list[HistoricalUploadRow] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(
                HistoricalUploadRow(
                    service_date=str(item["service_date"]),
                    service_window=str(item.get("service_window") or ServiceWindow.DINNER.value),
                    realized_total_covers=int(item["realized_total_covers"]),
                    realized_reserved_covers=_maybe_int(item.get("realized_reserved_covers")),
                    outside_covers=_maybe_int(item.get("outside_covers")),
                    service_state=str(item.get("service_state") or ServiceState.NORMAL.value),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _ai_review_summary(
    *,
    provider: AgentModelProvider | None,
    file_name: str,
    headers: list[str],
    sample_rows: list[dict[str, Any]],
) -> tuple[str | None, list[str], str]:
    if provider is None or not provider.is_available():
        return None, [], "medium"
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "confidence", "warnings"],
    }
    system_prompt = (
        "You review uploaded restaurant cover-history files for onboarding. "
        "Do not use technical language. Judge whether the columns and sample rows look like a usable "
        "12-month covers history upload. Focus on service date, total covers, likely dinner-only vs mixed-service, "
        "and whether optional service-state or patio/reservation splits appear present. "
        "Do not invent missing columns."
    )
    user_prompt = json.dumps(
        {
            "file_name": file_name,
            "headers": headers,
            "sample_rows": sample_rows[:5],
        },
        default=str,
    )
    try:
        result = provider.structured_json_call(
            system_prompt=f"{system_prompt}\nReturn JSON matching: {json.dumps(schema)}",
            user_prompt=user_prompt,
            max_output_tokens=250,
        )
    except Exception:
        return None, [], "medium"
    if not isinstance(result, dict):
        return None, [], "medium"
    summary = str(result.get("summary") or "").strip() or None
    warnings = [str(item).strip() for item in (result.get("warnings") or []) if str(item).strip()]
    confidence = str(result.get("confidence") or "medium").strip().lower() or "medium"
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"
    return summary, warnings, confidence


def review_and_stage_historical_upload(
    db: Database,
    *,
    file_name: str,
    content: str,
    provider: AgentModelProvider | None = None,
    default_service_window: ServiceWindow = ServiceWindow.DINNER,
) -> HistoricalUploadReview:
    raw_rows = _parse_csv_rows(content)
    headers = list(raw_rows[0].keys()) if raw_rows else []
    mapping = _detect_mapping(headers)
    normalized_rows: list[HistoricalUploadRow] = []
    skipped_rows = 0
    baseline_buckets: dict[str, list[int]] = {"mon_thu": [], "fri": [], "sat": [], "sun": []}
    season_bucket: dict[str, int] = {}

    service_date_header = mapping.get("service_date")
    total_header = mapping.get("realized_total_covers")

    for raw_row in raw_rows:
        service_date = _parse_date(raw_row.get(service_date_header)) if service_date_header else None
        realized_total_covers = _maybe_int(raw_row.get(total_header)) if total_header else None
        if service_date is None or realized_total_covers is None:
            skipped_rows += 1
            continue
        service_window = _parse_service_window(
            raw_row.get(mapping.get("service_window")),
            default_window=default_service_window,
        )
        if not is_runtime_window_supported(service_window):
            skipped_rows += 1
            continue
        service_state = _parse_service_state(raw_row.get(mapping.get("service_state")))
        row = HistoricalUploadRow(
            service_date=service_date.isoformat(),
            service_window=service_window.value,
            realized_total_covers=realized_total_covers,
            realized_reserved_covers=_maybe_int(raw_row.get(mapping.get("realized_reserved_covers"))),
            outside_covers=_maybe_int(raw_row.get(mapping.get("outside_covers"))),
            service_state=service_state.value,
        )
        normalized_rows.append(row)
        if service_state is ServiceState.NORMAL and service_window is default_service_window:
            baseline_buckets[day_group_for_date(service_date)].append(realized_total_covers)
            season_name = season_from_service_date(service_date)
            season_bucket[season_name] = season_bucket.get(season_name, 0) + 1

    first_date = min((_parse_date(row.service_date) for row in normalized_rows), default=None)
    last_date = max((_parse_date(row.service_date) for row in normalized_rows), default=None)
    distinct_months = len(
        {
            (parsed.year, parsed.month)
            for parsed in (_parse_date(row.service_date) for row in normalized_rows)
            if parsed is not None
        }
    )
    baseline_values = {
        key: int(round(sum(values) / len(values)))
        for key, values in baseline_buckets.items()
        if values
    }
    requirement_failures: list[str] = []
    warnings: list[str] = []
    if service_date_header is None:
        requirement_failures.append("Could not identify a service date column.")
    if total_header is None:
        requirement_failures.append("Could not identify a total covers column.")
    if distinct_months < 12:
        requirement_failures.append("At least 12 months of usable service history are required.")
    if len(baseline_values) < 4:
        requirement_failures.append("The upload must contain enough normal-service dinner rows to derive Mon-Thu, Fri, Sat, and Sun baselines.")
    if len(season_bucket) < 4:
        requirement_failures.append("The upload must cover all four seasons to build a local weather reference asset.")
    if any(count < 8 for count in season_bucket.values()):
        warnings.append("Some seasons have limited usable history, so the local weather reference may be less stable.")
    if not any(row.realized_reserved_covers is not None for row in normalized_rows):
        warnings.append("No reserved-cover split was found, so local weather training will treat total covers as the flexible demand target.")
    if not any(row.outside_covers is not None for row in normalized_rows):
        warnings.append("No patio/outside split was found, so outdoor seasonality will only be learned indirectly from total covers.")

    ai_summary, ai_warnings, ai_confidence = _ai_review_summary(
        provider=provider,
        file_name=file_name,
        headers=headers,
        sample_rows=raw_rows,
    )
    accepted = not requirement_failures and bool(normalized_rows)
    summary = (
        "The upload is ready to use for onboarding baselines and a local weather reference."
        if accepted
        else "The upload does not yet meet the historical setup requirements."
    )
    upload_token = f"hist_{uuid.uuid4().hex[:12]}"
    review = HistoricalUploadReview(
        accepted=accepted,
        summary=summary,
        upload_token=upload_token,
        file_name=file_name,
        usable_rows=len(normalized_rows),
        skipped_rows=skipped_rows,
        normal_service_rows=sum(1 for row in normalized_rows if row.service_state == ServiceState.NORMAL.value),
        distinct_months=distinct_months,
        seasons_covered=sorted(season_bucket.keys()),
        baseline_values=baseline_values,
        format_confidence=ai_confidence,
        warnings=warnings,
        ai_summary=ai_summary,
        ai_warnings=ai_warnings,
        requirement_failures=requirement_failures,
        mapping=mapping,
        first_service_date=first_date.isoformat() if first_date else None,
        last_service_date=last_date.isoformat() if last_date else None,
    )
    db.execute(
        """
        INSERT INTO historical_cover_uploads (
            upload_token, operator_id, file_name, review_status, review_json, normalized_rows_json, last_updated_at
        ) VALUES (?, NULL, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            upload_token,
            file_name,
            "accepted" if accepted else "rejected",
            json.dumps(asdict(review), default=str),
            _serialize_rows(normalized_rows),
        ],
    )
    return review


def load_staged_historical_upload(
    db: Database,
    *,
    upload_token: str,
) -> tuple[HistoricalUploadReview, list[HistoricalUploadRow]]:
    row = db.fetchone(
        """
        SELECT review_json, normalized_rows_json
        FROM historical_cover_uploads
        WHERE upload_token = ?
        """,
        [upload_token],
    )
    if row is None:
        raise ValueError("The reviewed history upload was not found.")
    review_dict = json.loads(str(row[0] or "{}"))
    review = HistoricalUploadReview(
        accepted=bool(review_dict.get("accepted")),
        summary=str(review_dict.get("summary") or ""),
        upload_token=str(review_dict.get("upload_token") or upload_token),
        file_name=str(review_dict.get("file_name") or ""),
        usable_rows=int(review_dict.get("usable_rows") or 0),
        skipped_rows=int(review_dict.get("skipped_rows") or 0),
        normal_service_rows=int(review_dict.get("normal_service_rows") or 0),
        distinct_months=int(review_dict.get("distinct_months") or 0),
        seasons_covered=[str(item) for item in (review_dict.get("seasons_covered") or [])],
        baseline_values={
            str(key): int(value)
            for key, value in dict(review_dict.get("baseline_values") or {}).items()
            if value is not None
        },
        format_confidence=str(review_dict.get("format_confidence") or "medium"),
        warnings=[str(item) for item in (review_dict.get("warnings") or [])],
        ai_summary=str(review_dict.get("ai_summary") or "").strip() or None,
        ai_warnings=[str(item) for item in (review_dict.get("ai_warnings") or [])],
        requirement_failures=[str(item) for item in (review_dict.get("requirement_failures") or [])],
        mapping={str(key): str(value) for key, value in dict(review_dict.get("mapping") or {}).items()},
        first_service_date=str(review_dict.get("first_service_date") or "").strip() or None,
        last_service_date=str(review_dict.get("last_service_date") or "").strip() or None,
    )
    return review, _deserialize_rows(str(row[1] or "[]"))


def claim_historical_upload_for_operator(
    db: Database,
    *,
    upload_token: str,
    operator_id: str,
) -> HistoricalUploadReview:
    review, _rows = load_staged_historical_upload(db, upload_token=upload_token)
    if not review.accepted:
        raise ValueError("This history upload has not passed review yet.")
    db.execute(
        """
        UPDATE historical_cover_uploads
        SET operator_id = ?, used_at = CURRENT_TIMESTAMP, last_updated_at = CURRENT_TIMESTAMP
        WHERE upload_token = ?
        """,
        [operator_id, upload_token],
    )
    return review


def load_operator_historical_upload_rows(
    db: Database,
    *,
    operator_id: str,
) -> tuple[str, list[HistoricalUploadRow]] | None:
    row = db.fetchone(
        """
        SELECT upload_token, normalized_rows_json
        FROM historical_cover_uploads
        WHERE operator_id = ?
          AND used_at IS NOT NULL
        ORDER BY used_at DESC, created_at DESC
        LIMIT 1
        """,
        [operator_id],
    )
    if row is None:
        return None
    return str(row[0]), _deserialize_rows(str(row[1] or "[]"))


def load_operator_historical_upload_review(
    db: Database,
    *,
    operator_id: str,
) -> HistoricalUploadReview | None:
    row = db.fetchone(
        """
        SELECT review_json
        FROM historical_cover_uploads
        WHERE operator_id = ?
          AND used_at IS NOT NULL
        ORDER BY used_at DESC, created_at DESC
        LIMIT 1
        """,
        [operator_id],
    )
    if row is None:
        return None
    review_dict = json.loads(str(row[0] or "{}"))
    if not isinstance(review_dict, dict):
        return None
    return HistoricalUploadReview(
        accepted=bool(review_dict.get("accepted")),
        summary=str(review_dict.get("summary") or ""),
        upload_token=str(review_dict.get("upload_token") or ""),
        file_name=str(review_dict.get("file_name") or ""),
        usable_rows=int(review_dict.get("usable_rows") or 0),
        skipped_rows=int(review_dict.get("skipped_rows") or 0),
        normal_service_rows=int(review_dict.get("normal_service_rows") or 0),
        distinct_months=int(review_dict.get("distinct_months") or 0),
        seasons_covered=[str(item) for item in (review_dict.get("seasons_covered") or [])],
        baseline_values={
            str(key): int(value)
            for key, value in dict(review_dict.get("baseline_values") or {}).items()
            if value is not None
        },
        format_confidence=str(review_dict.get("format_confidence") or "medium"),
        warnings=[str(item) for item in (review_dict.get("warnings") or [])],
        ai_summary=str(review_dict.get("ai_summary") or "").strip() or None,
        ai_warnings=[str(item) for item in (review_dict.get("ai_warnings") or [])],
        requirement_failures=[str(item) for item in (review_dict.get("requirement_failures") or [])],
        mapping={str(key): str(value) for key, value in dict(review_dict.get("mapping") or {}).items()},
        first_service_date=str(review_dict.get("first_service_date") or "").strip() or None,
        last_service_date=str(review_dict.get("last_service_date") or "").strip() or None,
    )
