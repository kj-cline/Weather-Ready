from __future__ import annotations


def precip_overlap_effect_pct(*, precip_prob: float, precip_dinner_max: float) -> float:
    precip_signal_strength = max(
        precip_prob,
        min(1.0, precip_dinner_max / 0.10) if precip_dinner_max > 0 else 0.0,
    )
    if precip_signal_strength < 0.60:
        return 0.0
    return -0.06 if precip_signal_strength < 0.80 else -0.10


def extreme_cold_effect_pct(*, apparent_temp: float) -> float:
    return -0.05 if apparent_temp < 35 else 0.0


def gray_suppression_effect_pct(*, cloudcover_bin: int, precip_prob: float, precip_dinner_max: float) -> float:
    precip_signal_strength = max(
        precip_prob,
        min(1.0, precip_dinner_max / 0.10) if precip_dinner_max > 0 else 0.0,
    )
    if cloudcover_bin >= 3 and precip_signal_strength < 0.60:
        return -0.03
    return 0.0
