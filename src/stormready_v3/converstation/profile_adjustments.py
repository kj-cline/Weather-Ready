from __future__ import annotations

from dataclasses import dataclass, field

from stormready_v3.domain.models import LocationContextProfile, OperatorProfile


@dataclass(slots=True)
class LocationHintAdjustment:
    patio_sensitivity_hint: float | None = None
    weather_sensitivity_hint: float | None = None
    confidence: str = "medium"
    summary: str = ""
    signals: list[str] = field(default_factory=list)
    fact_value: dict[str, object] = field(default_factory=dict)


def infer_weather_patio_adjustment(
    message: str,
    *,
    current_context: LocationContextProfile | None,
    operator_profile: OperatorProfile | None,
) -> LocationHintAdjustment | None:
    lowered = message.lower().strip()
    if len(lowered) < 8:
        return None

    patio_score = 0
    weather_score = 0
    signals: list[str] = []

    def _has_any(*phrases: str) -> bool:
        return any(phrase in lowered for phrase in phrases)

    patio_weather_high = [
        (
            "bad weather patio loss",
            "rain kills the patio",
            "rain kills patio",
            "rain usually cuts patio demand materially",
            "rain cuts patio demand materially",
            "patio dies in rain",
            "bad weather kills the patio",
            "weather kills the patio",
            "we lose the patio",
            "patio shuts down",
            "patio closes when",
            "outside shuts down",
        ),
        (
            "patio weather upside",
            "good weather fills the patio",
            "nice weather fills the patio",
            "good weather creates meaningful extra patio covers",
            "good weather creates extra patio covers",
            "patio fills up",
            "outside fills up",
            "patio gets packed",
            "outside gets packed",
            "patio is a big part",
            "outside is a big part",
        ),
        (
            "walk-in weather hit",
            "walk-ins drop when it rains",
            "walk ins drop when it rains",
            "walk-ins die in rain",
            "bad weather hurts walk-ins",
            "weather hurts walk-ins",
            "people stay home when it rains",
        ),
    ]
    patio_weather_low = [
        (
            "weather barely matters",
            "weather barely changes",
            "weather only changes patio demand a little",
            "weather only changes patio demand a little here",
            "weather does not matter much",
            "weather doesn't matter much",
            "weather is not a big factor",
            "only extreme weather matters",
        ),
        (
            "small patio exposure",
            "small patio",
            "tiny patio",
            "few patio seats",
            "most guests sit inside",
            "mostly indoor",
            "mostly inside",
            "the patio is minor",
        ),
        (
            "protected patio",
            "covered patio",
            "enclosed patio",
            "winter enclosed",
            "we can cover the patio",
        ),
    ]

    for label, *phrases in patio_weather_high:
        if _has_any(*phrases):
            signals.append(label)
            patio_score += 2
            weather_score += 1

    for label, *phrases in patio_weather_low:
        if _has_any(*phrases):
            signals.append(label)
            patio_score -= 2
            weather_score -= 1

    if _has_any(
        "rain hurts",
        "weather hurts",
        "snow hurts",
        "cold hurts",
        "heat hurts",
        "wind hurts",
        "storm hurts",
        "bad weather hurts demand",
        "bad weather hurts business",
    ):
        signals.append("general_weather_downside")
        weather_score += 2

    if _has_any(
        "nice weather helps",
        "good weather helps",
        "good weather boosts us",
        "warm weather helps",
        "sunny weather helps",
    ):
        signals.append("general_weather_upside")
        weather_score += 1

    if _has_any(
        "weather barely matters",
        "weather does not matter",
        "weather doesn't matter",
        "inside stays steady",
        "indoor stays steady",
    ):
        signals.append("general_weather_resilient")
        weather_score -= 2

    if not signals:
        return None

    patio_level = _classify_level(patio_score, high_threshold=3, low_threshold=-2)
    weather_level = _classify_level(weather_score, high_threshold=3, low_threshold=-2)

    patio_target = None
    if operator_profile is None or operator_profile.patio_enabled:
        patio_target = {
            "high": 1.65,
            "medium": 1.30,
            "low": 0.90,
        }[patio_level]

    weather_target = {
        "high": 1.18,
        "medium": 1.05,
        "low": 0.92,
    }[weather_level]

    patio_hint = _blend_hint(
        current_context.patio_sensitivity_hint if current_context is not None else None,
        patio_target,
        weight=0.65,
    )
    weather_hint = _blend_hint(
        current_context.weather_sensitivity_hint if current_context is not None else None,
        weather_target,
        weight=0.65,
    )

    summary_parts: list[str] = []
    if patio_target is not None:
        summary_parts.append(_level_summary("patio weather exposure", patio_level))
    else:
        summary_parts.append("weather sensitivity updated without a patio-specific adjustment")
    summary_parts.append(_level_summary("overall weather sensitivity", weather_level))

    fact_value: dict[str, object] = {
        "kind": "weather_patio_profile_adjustment",
        "source_note": message.strip(),
        "signals": signals,
        "patio_level": patio_level if patio_target is not None else None,
        "weather_level": weather_level,
        "patio_sensitivity_hint": patio_hint,
        "weather_sensitivity_hint": weather_hint,
    }

    return LocationHintAdjustment(
        patio_sensitivity_hint=patio_hint,
        weather_sensitivity_hint=weather_hint,
        confidence="high" if len(signals) >= 2 else "medium",
        summary="; ".join(summary_parts),
        signals=signals,
        fact_value=fact_value,
    )


def _classify_level(score: int, *, high_threshold: int, low_threshold: int) -> str:
    if score >= high_threshold:
        return "high"
    if score <= low_threshold:
        return "low"
    return "medium"


def _blend_hint(current: float | None, target: float | None, *, weight: float) -> float | None:
    if target is None:
        return None
    if current is None:
        return round(target, 2)
    blended = (float(current) * (1.0 - weight)) + (float(target) * weight)
    return round(blended, 2)


def _level_summary(label: str, level: str) -> str:
    if level == "high":
        return f"{label} should be treated as higher"
    if level == "low":
        return f"{label} should be treated as lower"
    return f"{label} should stay moderate"
