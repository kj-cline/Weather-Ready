from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from stormready_v3.operator_text import communication_payload, communication_text_from_state, driver_label, uncertainty_label


DEFAULT_ATTENTION_SECTION_ORDER = (
    "pending_operator_action",
    "current_operational_watchout",
    "current_uncertainty",
    "latest_material_change",
    "best_next_question",
)

AUTHORITATIVE_SERVICE_STATE_SOURCES = {"operator", "connected_truth", "calendar_rule"}


def build_operator_attention_summary(
    *,
    reference_date: date,
    current_time: datetime | None,
    actionable_forecasts: list[dict[str, Any]],
    recent_snapshots: list[dict[str, Any]],
    open_service_state_suggestions: list[dict[str, Any]],
    pending_corrections: list[dict[str, Any]],
    missing_actuals: list[dict[str, Any]],
    service_plan_window: dict[str, Any] | None,
    learning_agenda: list[dict[str, Any]],
    open_hypotheses: list[dict[str, Any]],
    recent_learning_decisions: list[dict[str, Any]],
    engine_digests: list[dict[str, Any]],
) -> dict[str, Any]:
    best_next_question = _best_next_question(learning_agenda=learning_agenda, open_hypotheses=open_hypotheses)
    sections = {
        "latest_material_change": _latest_material_change(recent_snapshots),
        "current_operational_watchout": _current_operational_watchout(
            actionable_forecasts=actionable_forecasts,
            open_service_state_suggestions=open_service_state_suggestions,
        ),
        "pending_operator_action": _pending_operator_action(
            pending_corrections=pending_corrections,
            missing_actuals=missing_actuals,
            service_plan_window=service_plan_window,
            learning_agenda=learning_agenda,
            best_next_question=best_next_question,
        ),
        "current_uncertainty": _current_uncertainty(
            actionable_forecasts=actionable_forecasts,
            engine_digests=engine_digests,
            recent_learning_decisions=recent_learning_decisions,
        ),
        "best_next_question": best_next_question,
    }
    sections = _dedupe_attention_sections(sections)
    operating_moment = _operating_moment(reference_date=reference_date, current_time=current_time)
    ordered_section_keys = _ordered_section_keys(operating_moment=operating_moment)
    primary_focus_key = next(
        (key for key in ordered_section_keys if _has_attention_content(sections.get(key))),
        None,
    )
    return {
        "operating_moment": operating_moment,
        "moment_label": _moment_label(operating_moment),
        "primary_focus_key": primary_focus_key,
        "ordered_section_keys": ordered_section_keys,
        **sections,
    }


def ordered_attention_sections(summary: dict[str, Any] | None) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(summary, dict):
        return []
    keys: list[str] = []
    for key in list(summary.get("ordered_section_keys") or []):
        if isinstance(key, str) and key not in keys:
            keys.append(key)
    for key in DEFAULT_ATTENTION_SECTION_ORDER:
        if key not in keys:
            keys.append(key)
    ordered: list[tuple[str, dict[str, Any]]] = []
    for key in keys:
        value = summary.get(key)
        if isinstance(value, dict):
            ordered.append((key, value))
    return ordered


def _latest_material_change(recent_snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    changed = [item for item in recent_snapshots if item.get("changed")]
    if not changed:
        return None
    ranked = sorted(
        changed,
        key=lambda item: (
            _sort_dt(item.get("snapshot_at")),
            abs(int(item.get("delta_expected") or 0)),
        ),
        reverse=True,
    )
    selected = ranked[0]
    delta = int(selected.get("delta_expected") or 0)
    direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
    top_driver = _first_text(selected.get("top_drivers"))
    return {
        "service_date": selected.get("service_date"),
        "service_window": selected.get("service_window"),
        "before_expected": selected.get("previous_expected"),
        "now_expected": selected.get("forecast_expected"),
        "delta_expected": delta,
        "direction": direction,
        "snapshot_reason": selected.get("snapshot_reason"),
        "top_driver": top_driver,
        "communication_payload": communication_payload(
            category="published_change",
            what_is_true_now=(
                f"{_service_label(service_date=selected.get('service_date'), service_window=selected.get('service_window')) or 'This dinner'} "
                f"moved {'up' if delta > 0 else 'down' if delta < 0 else 'sideways'} "
                f"from {selected.get('previous_expected')} to {selected.get('forecast_expected')} covers."
            ),
            why_it_matters=(f"Main driver: {driver_label(top_driver)}." if top_driver else None),
            facts={
                "service_date": str(selected.get("service_date") or ""),
                "before_expected": selected.get("previous_expected"),
                "now_expected": selected.get("forecast_expected"),
                "delta_expected": delta,
            },
        ),
    }


def _current_operational_watchout(
    *,
    actionable_forecasts: list[dict[str, Any]],
    open_service_state_suggestions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if open_service_state_suggestions:
        suggestion = open_service_state_suggestions[0]
        service_label = _service_label(
            service_date=suggestion.get("service_date"),
            service_window=suggestion.get("service_window"),
        )
        return {
            "watchout_type": "service_state_suggestion",
            "service_date": suggestion.get("service_date"),
            "service_window": suggestion.get("service_window"),
            "service_state": suggestion.get("service_state"),
            "communication_payload": communication_payload(
                category="working_signal",
                what_is_true_now=(
                    f"I may have the wrong service setup for {service_label}."
                    if service_label
                    else "I may have the wrong service setup."
                ),
                what_i_need_from_you="Please confirm what service will actually look like.",
                facts={"service_date": str(suggestion.get("service_date") or ""), "service_state": suggestion.get("service_state")},
            ),
            "confidence": suggestion.get("confidence"),
            "source_type": suggestion.get("source_type"),
        }

    abnormal = _select_watch_card(
        actionable_forecasts,
        predicate=_service_state_needs_confirmation,
    )
    if abnormal is not None:
        top_driver = _first_text(abnormal.get("top_drivers"))
        service_label = _service_label(
            service_date=abnormal.get("service_date"),
            service_window=abnormal.get("service_window"),
        )
        return {
            "watchout_type": "abnormal_service_forecast",
            "service_date": abnormal.get("service_date"),
            "service_window": abnormal.get("service_window"),
            "service_state": abnormal.get("service_state"),
            "communication_payload": communication_payload(
                category="working_signal",
                what_is_true_now=(
                    f"{service_label} may not run as planned."
                    if service_label
                    else "Service may not run as planned."
                ),
                why_it_matters=(f"Main driver: {driver_label(top_driver)}." if top_driver else None),
                what_is_still_uncertain=(
                    uncertainty_label(_first_text(abnormal.get("major_uncertainties")) or "")
                    if _first_text(abnormal.get("major_uncertainties"))
                    else None
                ),
                facts={"service_date": str(abnormal.get("service_date") or ""), "service_state": abnormal.get("service_state")},
            ),
            "confidence_tier": abnormal.get("confidence_tier"),
            "top_driver": top_driver,
        }

    near_term_focus = _select_watch_card(
        actionable_forecasts,
        predicate=lambda card: bool(card.get("major_uncertainties"))
        or str(card.get("confidence_tier") or "").lower() in {"low", "very_low"},
        near_term_only=True,
    )
    if near_term_focus is not None:
        top_driver = _first_text(near_term_focus.get("top_drivers"))
        service_label = _service_label(
            service_date=near_term_focus.get("service_date"),
            service_window=near_term_focus.get("service_window"),
        )
        if str(near_term_focus.get("confidence_tier") or "").lower() in {"low", "very_low"}:
            return {
                "watchout_type": "low_confidence_forecast",
                "service_date": near_term_focus.get("service_date"),
                "service_window": near_term_focus.get("service_window"),
                "communication_payload": communication_payload(
                    category="working_signal",
                    what_is_true_now=(
                        f"{service_label} is still settling."
                        if service_label
                        else "The near-term forecast is still settling."
                    ),
                    why_it_matters=(f"Main driver: {driver_label(top_driver)}." if top_driver else None),
                    what_is_still_uncertain=(
                        uncertainty_label(_first_text(near_term_focus.get("major_uncertainties")) or "")
                        if _first_text(near_term_focus.get("major_uncertainties"))
                        else None
                    ),
                    facts={"service_date": str(near_term_focus.get("service_date") or ""), "confidence_tier": near_term_focus.get("confidence_tier")},
                ),
                "confidence_tier": near_term_focus.get("confidence_tier"),
                "top_driver": top_driver,
            }
        return {
            "watchout_type": "forecast_uncertainty",
            "service_date": near_term_focus.get("service_date"),
            "service_window": near_term_focus.get("service_window"),
            "communication_payload": communication_payload(
                category="working_signal",
                what_is_still_uncertain=(
                    f"{service_label}: {uncertainty_label(_first_text(near_term_focus.get('major_uncertainties')) or '')}"
                    if service_label and _first_text(near_term_focus.get("major_uncertainties"))
                    else uncertainty_label(_first_text(near_term_focus.get("major_uncertainties")) or "") or "The near-term forecast is still settling."
                ),
                facts={"service_date": str(near_term_focus.get("service_date") or "")},
            ),
            "confidence_tier": near_term_focus.get("confidence_tier"),
            "top_driver": top_driver,
        }

    low_confidence = _select_watch_card(
        actionable_forecasts,
        predicate=lambda card: str(card.get("confidence_tier") or "").lower() in {"low", "very_low"},
    )
    if low_confidence is not None:
        top_driver = _first_text(low_confidence.get("top_drivers"))
        service_label = _service_label(
            service_date=low_confidence.get("service_date"),
            service_window=low_confidence.get("service_window"),
        )
        return {
            "watchout_type": "low_confidence_forecast",
            "service_date": low_confidence.get("service_date"),
            "service_window": low_confidence.get("service_window"),
            "communication_payload": communication_payload(
                category="working_signal",
                what_is_true_now=(
                    f"{service_label} is still settling."
                    if service_label
                    else "The forecast is still settling."
                ),
                why_it_matters=(f"Main driver: {driver_label(top_driver)}." if top_driver else None),
                what_is_still_uncertain=(
                    uncertainty_label(_first_text(low_confidence.get("major_uncertainties")) or "")
                    if _first_text(low_confidence.get("major_uncertainties"))
                    else None
                ),
                facts={"service_date": str(low_confidence.get("service_date") or ""), "confidence_tier": low_confidence.get("confidence_tier")},
            ),
            "confidence_tier": low_confidence.get("confidence_tier"),
            "top_driver": top_driver,
        }

    uncertain = _select_watch_card(
        actionable_forecasts,
        predicate=lambda card: bool(card.get("major_uncertainties")),
    )
    if uncertain is not None:
        uncertainty = _first_text(uncertain.get("major_uncertainties"))
        service_label = _service_label(
            service_date=uncertain.get("service_date"),
            service_window=uncertain.get("service_window"),
        )
        return {
            "watchout_type": "forecast_uncertainty",
            "service_date": uncertain.get("service_date"),
            "service_window": uncertain.get("service_window"),
            "communication_payload": communication_payload(
                category="working_signal",
                what_is_still_uncertain=(
                    f"{service_label}: {uncertainty_label(uncertainty or '')}"
                    if service_label and uncertainty
                    else uncertainty_label(uncertainty or "") or "The forecast is still settling."
                ),
                why_it_matters=(
                    f"Main driver: {driver_label(_first_text(uncertain.get('top_drivers')) or '')}."
                    if _first_text(uncertain.get("top_drivers"))
                    else None
                ),
                facts={"service_date": str(uncertain.get("service_date") or "")},
            ),
            "confidence_tier": uncertain.get("confidence_tier"),
            "top_driver": _first_text(uncertain.get("top_drivers")),
        }
    return None


def _pending_operator_action(
    *,
    pending_corrections: list[dict[str, Any]],
    missing_actuals: list[dict[str, Any]],
    service_plan_window: dict[str, Any] | None,
    learning_agenda: list[dict[str, Any]],
    best_next_question: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if missing_actuals:
        target = missing_actuals[0]
        service_label = _service_label(
            service_date=target.get("service_date"),
            service_window=target.get("service_window"),
        )
        forecast_expected = target.get("forecast_expected")
        forecast_fragment = f" That dinner was forecast at {forecast_expected} covers." if forecast_expected is not None else ""
        summary = (
            f"Please use the actuals form to log the actual covers for {service_label}.{forecast_fragment}"
            if service_label
            else f"Please use the actuals form to log the latest actual covers.{forecast_fragment}"
        )
        return {
            "action_type": "open_actuals_form",
            "service_date": target.get("service_date"),
            "service_window": target.get("service_window"),
            "communication_payload": communication_payload(
                category="workflow_obligation",
                what_i_need_from_you=summary.strip(),
                facts={"service_date": str(target.get("service_date") or ""), "forecast_expected": target.get("forecast_expected")},
            ),
            "forecast_expected": target.get("forecast_expected"),
            "confidence_tier": target.get("confidence_tier"),
        }
    if pending_corrections:
        target = pending_corrections[0]
        service_label = _service_label(
            service_date=target.get("service_date"),
            service_window=target.get("service_window"),
        )
        return {
            "action_type": "review_correction",
            "service_date": target.get("service_date"),
            "service_window": target.get("service_window"),
            "communication_payload": communication_payload(
                category="workflow_obligation",
                what_i_need_from_you=(
                    f"I have a correction ready for {service_label}, and I still need your review before I apply it."
                    if service_label
                    else "I have a correction ready, and I still need your review before I apply it."
                ),
                facts={"service_date": str(target.get("service_date") or ""), "suggestion_id": target.get("suggestion_id")},
            ),
            "suggested_service_state": target.get("suggested_service_state"),
            "source_type": target.get("source_type"),
            "suggestion_id": target.get("suggestion_id"),
        }
    if service_plan_window and int(service_plan_window.get("due_count") or 0) > 0:
        pending_dates = [
            _format_attention_date(item)
            for item in list(service_plan_window.get("pending_dates") or [])[:4]
        ]
        pending_dates_text = ", ".join(date_label for date_label in pending_dates if date_label)
        due_count = int(service_plan_window.get("due_count") or 0)
        if pending_dates_text:
            summary = (
                f"I still need your plan for {pending_dates_text} ({due_count} night{'' if due_count == 1 else 's'})."
            )
        else:
            summary = (
                f"I still need your operating plan for {due_count} upcoming night{'' if due_count == 1 else 's'}."
            )
        return {
            "action_type": "review_service_plan",
            "communication_payload": communication_payload(
                category="workflow_obligation",
                what_i_need_from_you=summary.strip(),
                facts={
                    "due_count": service_plan_window.get("due_count"),
                    "pending_dates": list(service_plan_window.get("pending_dates") or [])[:4],
                },
            ),
            "window_label": service_plan_window.get("window_label"),
            "due_count": service_plan_window.get("due_count"),
            "pending_dates": list(service_plan_window.get("pending_dates") or [])[:4],
        }
    reminders = sorted(
        [
            item
            for item in learning_agenda
            if str(item.get("status") or "open") == "open"
            and str(item.get("question_kind") or "") == "reminder"
            and not _is_in_cooldown(item.get("cooldown_until"))
        ],
        key=_agenda_sort_key,
    )
    if reminders:
        reminder = reminders[0]
        payload = reminder.get("communication_payload") or communication_payload(
            category="workflow_obligation",
            what_i_need_from_you="I still need one open follow-through item from you.",
        )
        return {
            "action_type": "reminder",
            "communication_payload": payload,
            "agenda_key": reminder.get("agenda_key"),
            "rationale": reminder.get("rationale"),
            "expected_impact": reminder.get("expected_impact"),
        }
    if best_next_question is not None:
        payload = best_next_question.get("communication_payload")
        return {
            "action_type": "answer_learning_question",
            "communication_payload": payload,
            "agenda_key": best_next_question.get("agenda_key"),
            "expected_impact": best_next_question.get("expected_impact"),
        }
    return None


def _current_uncertainty(
    *,
    actionable_forecasts: list[dict[str, Any]],
    engine_digests: list[dict[str, Any]],
    recent_learning_decisions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    digest_by_day = {
        (digest.get("service_date"), digest.get("service_window")): digest
        for digest in engine_digests
    }
    for card in actionable_forecasts:
        uncertainty = _first_text(card.get("major_uncertainties"))
        digest = digest_by_day.get((card.get("service_date"), card.get("service_window")))
        source_failure_count = int((digest or {}).get("source_failure_count") or 0)
        connector_failure_count = int((digest or {}).get("connector_failure_count") or 0)
        service_label = _service_label(
            service_date=card.get("service_date"),
            service_window=card.get("service_window"),
        )
        if uncertainty or source_failure_count > 0 or connector_failure_count > 0:
            return {
                "service_date": card.get("service_date"),
                "service_window": card.get("service_window"),
                "confidence_tier": card.get("confidence_tier"),
                "top_uncertainty": uncertainty,
                "source_failure_count": source_failure_count,
                "connector_failure_count": connector_failure_count,
                "communication_payload": communication_payload(
                    category="working_signal",
                    what_is_still_uncertain=(
                        (f"{service_label}: " if service_label else "")
                        + _uncertainty_summary(
                            uncertainty=uncertainty,
                            source_failure_count=source_failure_count,
                            connector_failure_count=connector_failure_count,
                        )
                    ),
                    facts={
                        "service_date": str(card.get("service_date") or ""),
                        "source_failure_count": source_failure_count,
                        "connector_failure_count": connector_failure_count,
                    },
                ),
            }
    if recent_learning_decisions:
        decision = recent_learning_decisions[0]
        return {
            "communication_payload": communication_payload(
                category="working_signal",
                what_is_still_uncertain="I recently adjusted one pattern here, so the next few services may still settle a bit.",
                facts={
                    "decision_type": decision.get("decision_type"),
                    "runtime_target": decision.get("runtime_target"),
                },
            ),
            "decision_type": decision.get("decision_type"),
            "runtime_target": decision.get("runtime_target"),
            "equation_terms": decision.get("equation_terms"),
        }
    return None


def _best_next_question(
    *,
    learning_agenda: list[dict[str, Any]],
    open_hypotheses: list[dict[str, Any]],
) -> dict[str, Any] | None:
    open_questions = sorted(
        [
            item
            for item in learning_agenda
            if str(item.get("status") or "open") == "open"
            and str(item.get("question_kind") or "") in {"yes_no", "free_text"}
            and not _is_in_cooldown(item.get("cooldown_until"))
        ],
        key=_agenda_sort_key,
    )
    if not open_questions:
        return None
    hypothesis_index = {
        str(item.get("hypothesis_key") or ""): item
        for item in open_hypotheses
        if item.get("hypothesis_key")
    }
    question = open_questions[0]
    hypothesis = hypothesis_index.get(str(question.get("hypothesis_key") or ""))
    hypothesis_value = (hypothesis or {}).get("hypothesis_value") or {}
    return {
        "agenda_key": question.get("agenda_key"),
        "question_kind": question.get("question_kind"),
        "communication_payload": question.get("communication_payload") or (hypothesis_value or {}).get("communication_payload"),
        "rationale": question.get("rationale"),
        "expected_impact": question.get("expected_impact"),
        "service_date": question.get("service_date"),
        "hypothesis_key": question.get("hypothesis_key"),
        "runtime_target": hypothesis_value.get("runtime_target"),
        "equation_terms": list(hypothesis_value.get("equation_terms") or []),
        "equation_path": hypothesis_value.get("equation_path"),
        "equation_influence_mode": hypothesis_value.get("equation_influence_mode"),
        "learning_stage": hypothesis_value.get("learning_stage"),
    }


def _operating_moment(*, reference_date: date, current_time: datetime | None) -> str:
    current_date = current_time.date() if current_time is not None else date.today()
    if reference_date < current_date:
        return "historical_review"
    if reference_date > current_date:
        return "forward_planning"
    hour = current_time.hour if current_time is not None else datetime.now(UTC).hour
    if hour < 11:
        return "morning_planning"
    if hour < 15:
        return "midday_check"
    if hour < 18:
        return "pre_service"
    if hour < 22:
        return "service_live"
    return "post_service_review"


def _moment_label(moment: str) -> str:
    labels = {
        "historical_review": "Looking back",
        "forward_planning": "Planning ahead",
        "morning_planning": "Morning plan",
        "midday_check": "Midday update",
        "pre_service": "Before service",
        "service_live": "During service",
        "post_service_review": "After service",
    }
    return labels.get(moment, "Current focus")


def _ordered_section_keys(*, operating_moment: str) -> list[str]:
    if operating_moment in {"pre_service", "service_live"}:
        return [
            "current_operational_watchout",
            "pending_operator_action",
            "current_uncertainty",
            "latest_material_change",
            "best_next_question",
        ]
    if operating_moment == "midday_check":
        return [
            "current_operational_watchout",
            "pending_operator_action",
            "latest_material_change",
            "current_uncertainty",
            "best_next_question",
        ]
    if operating_moment == "forward_planning":
        return [
            "pending_operator_action",
            "latest_material_change",
            "current_operational_watchout",
            "current_uncertainty",
            "best_next_question",
        ]
    if operating_moment in {"historical_review", "post_service_review"}:
        return [
            "pending_operator_action",
            "current_uncertainty",
            "latest_material_change",
            "best_next_question",
            "current_operational_watchout",
        ]
    return list(DEFAULT_ATTENTION_SECTION_ORDER)


def _has_attention_content(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    text = _attention_section_text(value)
    return bool(text)


def _service_state_needs_confirmation(card: dict[str, Any]) -> bool:
    service_state = str(card.get("service_state") or "").lower()
    if service_state in {"", "normal", "normal_service"}:
        return False
    source = str(card.get("service_state_source") or "").lower()
    reason = str(card.get("service_state_reason") or "").lower()
    if source in AUTHORITATIVE_SERVICE_STATE_SOURCES:
        return False
    if reason in {"explicit operator input", "connected system truth", "calendar or holiday rule"}:
        return False
    return True


def _dedupe_attention_sections(sections: dict[str, Any]) -> dict[str, Any]:
    watchout = sections.get("current_operational_watchout")
    uncertainty = sections.get("current_uncertainty")
    if isinstance(watchout, dict) and isinstance(uncertainty, dict):
        same_date = str(watchout.get("service_date") or "") == str(uncertainty.get("service_date") or "")
        same_window = str(watchout.get("service_window") or "") == str(uncertainty.get("service_window") or "")
        watchout_summary = _normalize_attention_text(_attention_section_text(watchout))
        uncertainty_summary = _normalize_attention_text(_attention_section_text(uncertainty))
        uncertainty_text = _normalize_attention_text(
            uncertainty_label(str(uncertainty.get("top_uncertainty") or ""))
        )
        if same_date and same_window and (
            watchout_summary == uncertainty_summary
            or (uncertainty_summary and uncertainty_summary in watchout_summary)
            or (uncertainty_text and uncertainty_text in watchout_summary)
        ):
            sections["current_uncertainty"] = None
    return sections


def _normalize_attention_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or ch.isspace())


def _attention_section_text(value: dict[str, Any] | None) -> str:
    return communication_text_from_state(value, include_question=True)


def _is_in_cooldown(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, datetime):
        cooldown_until = value
    else:
        try:
            cooldown_until = datetime.fromisoformat(str(value))
        except ValueError:
            return False
    if cooldown_until.tzinfo is not None:
        cooldown_until = cooldown_until.astimezone(UTC).replace(tzinfo=None)
    return cooldown_until > datetime.now(UTC).replace(tzinfo=None)


def _sort_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    if value is None:
        return datetime.min
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _select_watch_card(
    actionable_forecasts: list[dict[str, Any]],
    *,
    predicate: Any,
    near_term_only: bool = False,
) -> dict[str, Any] | None:
    if not actionable_forecasts:
        return None

    ranked = [
        card
        for card in actionable_forecasts
        if predicate(card)
    ]
    if not ranked:
        return None

    anchor = _card_service_date(actionable_forecasts[0]) or date.today()
    if near_term_only:
        near_term_ranked = [
            card
            for card in ranked
            if ((_card_service_date(card) or anchor) - anchor).days <= 1
        ]
        if near_term_ranked:
            ranked = near_term_ranked

    def sort_key(card: dict[str, Any]) -> tuple[int, int, int]:
        service_date = _card_service_date(card) or anchor
        day_offset = max(0, (service_date - anchor).days)
        near_term_penalty = 0 if day_offset <= 1 else 1
        severity_rank = 0 if str(card.get("confidence_tier") or "").lower() == "very_low" else 1
        return (near_term_penalty, day_offset, severity_rank)

    return sorted(ranked, key=sort_key)[0]


def _card_service_date(card: dict[str, Any]) -> date | None:
    raw_value = card.get("service_date")
    if isinstance(raw_value, date):
        return raw_value
    if raw_value is None:
        return None
    try:
        return date.fromisoformat(str(raw_value))
    except ValueError:
        return None


def _format_attention_date(value: Any) -> str | None:
    if isinstance(value, date):
        return value.strftime("%b %d").replace(" 0", " ")
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return str(value).strip() or None
    return parsed.strftime("%b %d").replace(" 0", " ")


def _service_label(*, service_date: Any, service_window: Any) -> str:
    date_label = _format_attention_date(service_date)
    window_label = str(service_window or "").replace("_", " ").strip()
    if date_label and window_label:
        return f"{date_label} {window_label}"
    return date_label or window_label


def _first_text(value: Any) -> str | None:
    if isinstance(value, list):
        if not value:
            return None
        first = value[0]
        return str(first).strip() if first not in {None, ""} else None
    if value not in {None, ""}:
        return str(value).strip()
    return None


def _uncertainty_summary(
    *,
    uncertainty: str | None,
    source_failure_count: int,
    connector_failure_count: int,
) -> str:
    parts: list[str] = []
    if uncertainty:
        parts.append(uncertainty_label(str(uncertainty)))
    if source_failure_count > 0:
        parts.append(
            "some outside signals did not refresh normally"
            if source_failure_count == 1
            else f"{source_failure_count} outside signals did not refresh normally"
        )
    if connector_failure_count > 0:
        parts.append(
            "one connected system may be stale"
            if connector_failure_count == 1
            else f"{connector_failure_count} connected systems may be stale"
        )
    if not parts:
        return "A few things are still in play."
    return "; ".join(parts) + "."


def _agenda_sort_key(item: dict[str, Any]) -> tuple[int, date, datetime]:
    priority = item.get("priority")
    if isinstance(priority, (int, float)):
        priority_rank = -int(priority)
    else:
        priority_rank = 0
    service_date = _card_service_date({"service_date": item.get("service_date")}) or date.max
    created_at = _sort_dt(item.get("asked_at") or item.get("created_at"))
    return (priority_rank, service_date, created_at)
