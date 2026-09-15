"""Pure telemetry profiling helpers for Battery Health Analyzer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from math import sqrt
from statistics import median

from .models import (
    BatteryHistorySummary,
    BehaviorClassification,
    NumericHistorySummary,
    RelationClassification,
    TelemetryProfile,
    VoltageHistorySummary,
)

MIN_PROFILE_DAYS = 5
STATIC_SPAN_PERCENT = 2.0
MONOTONIC_MIN_SPAN_PERCENT = 3.0
MONOTONIC_DIRECTIONAL_RATIO = 0.8
VOLATILE_MIN_SPAN_PERCENT = 10.0
RELATION_MIN_PAIRS = 7
BATTERY_VOLTAGE_DERIVED_CORRELATION = 0.95
STRONG_RELATION_CORRELATION = 0.8
MODERATE_RELATION_CORRELATION = 0.6


def _finite_values(values: Iterable[float | None]) -> list[float]:
    """Return finite numeric values only."""
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            continue
        result.append(numeric)
    return result


def stable_upper_envelope(
    daily_p90_values: Iterable[float | None],
) -> float | None:
    """Return the robust upper envelope as the median of daily p90 values."""
    values = _finite_values(daily_p90_values)
    return float(median(values)) if values else None


def classify_battery_behavior(
    daily_values: Sequence[float | None],
) -> BehaviorClassification:
    """Classify long-term battery percentage reporting behavior."""
    values = _finite_values(daily_values)
    valid_days = len(values)
    if valid_days < MIN_PROFILE_DAYS:
        return BehaviorClassification("insufficient", 0.0, valid_days)

    span = max(values) - min(values)
    if span <= STATIC_SPAN_PERCENT:
        confidence = 1.0 if span == 0 else max(0.7, 1.0 - span / 10.0)
        return BehaviorClassification("static", confidence, valid_days)

    changes = [
        current - previous
        for previous, current in zip(values, values[1:])
        if current != previous
    ]
    if not changes:
        return BehaviorClassification("static", 1.0, valid_days)

    positive = sum(change > 0 for change in changes)
    negative = sum(change < 0 for change in changes)
    directional_ratio = max(positive, negative) / len(changes)
    reversals = sum(
        (first < 0 < second) or (first > 0 > second)
        for first, second in zip(changes, changes[1:])
    )

    if (
        span >= MONOTONIC_MIN_SPAN_PERCENT
        and directional_ratio >= MONOTONIC_DIRECTIONAL_RATIO
        and reversals <= 1
    ):
        confidence = min(1.0, 0.7 + 0.3 * directional_ratio)
        return BehaviorClassification("monotonic", confidence, valid_days)

    if span >= VOLATILE_MIN_SPAN_PERCENT and positive and negative:
        confidence = min(1.0, 0.7 + min(span, 50.0) / 100.0)
        return BehaviorClassification("volatile", confidence, valid_days)

    confidence = min(0.9, 0.5 + min(span, 40.0) / 100.0)
    return BehaviorClassification("mixed", confidence, valid_days)


def pearson_correlation(
    first: Sequence[float | None],
    second: Sequence[float | None],
) -> tuple[float | None, int]:
    """Return Pearson correlation and number of valid paired samples."""
    pairs: list[tuple[float, float]] = []
    for left, right in zip(first, second):
        if left is None or right is None:
            continue
        left_value = float(left)
        right_value = float(right)
        if (
            left_value != left_value
            or right_value != right_value
            or left_value in (float("inf"), float("-inf"))
            or right_value in (float("inf"), float("-inf"))
        ):
            continue
        pairs.append((left_value, right_value))

    paired_days = len(pairs)
    if paired_days < 3:
        return None, paired_days

    left_values = [left for left, _ in pairs]
    right_values = [right for _, right in pairs]
    left_mean = sum(left_values) / paired_days
    right_mean = sum(right_values) / paired_days
    left_delta = [value - left_mean for value in left_values]
    right_delta = [value - right_mean for value in right_values]
    left_energy = sum(value * value for value in left_delta)
    right_energy = sum(value * value for value in right_delta)
    if left_energy == 0 or right_energy == 0:
        return None, paired_days

    covariance = sum(left * right for left, right in zip(left_delta, right_delta))
    correlation = covariance / sqrt(left_energy * right_energy)
    return max(-1.0, min(1.0, correlation)), paired_days


def classify_battery_voltage_relation(
    battery_daily: Sequence[float | None],
    voltage_daily: Sequence[float | None],
) -> RelationClassification:
    """Classify whether reported battery percentage largely mirrors voltage."""
    correlation, paired_days = pearson_correlation(battery_daily, voltage_daily)
    if paired_days < RELATION_MIN_PAIRS or correlation is None:
        return RelationClassification("insufficient", correlation, paired_days)
    if correlation >= BATTERY_VOLTAGE_DERIVED_CORRELATION:
        relation = "derived"
    elif abs(correlation) >= STRONG_RELATION_CORRELATION:
        relation = "strong"
    elif abs(correlation) >= MODERATE_RELATION_CORRELATION:
        relation = "moderate"
    else:
        relation = "weak"
    return RelationClassification(relation, correlation, paired_days)


def classify_temperature_relation(
    voltage_daily: Sequence[float | None],
    temperature_daily: Sequence[float | None],
) -> RelationClassification:
    """Classify the long-term voltage-to-temperature relationship."""
    correlation, paired_days = pearson_correlation(voltage_daily, temperature_daily)
    if paired_days < RELATION_MIN_PAIRS or correlation is None:
        return RelationClassification("insufficient", correlation, paired_days)
    magnitude = abs(correlation)
    if magnitude >= STRONG_RELATION_CORRELATION:
        strength = "strong"
    elif magnitude >= MODERATE_RELATION_CORRELATION:
        strength = "moderate"
    else:
        strength = "weak"
    direction = "positive" if correlation >= 0 else "negative"
    return RelationClassification(
        f"{strength}_{direction}",
        correlation,
        paired_days,
    )


def build_telemetry_profile(
    battery_daily: Mapping[str, BatteryHistorySummary],
    voltage_daily: Mapping[str, VoltageHistorySummary],
    temperature_daily: Mapping[str, NumericHistorySummary] | None = None,
) -> TelemetryProfile:
    """Build one compact profile from aligned complete-day summaries."""
    temperatures = temperature_daily or {}
    days = sorted(set(battery_daily) | set(voltage_daily) | set(temperatures))

    battery_median = [
        battery_daily[day].median_percent if day in battery_daily else None
        for day in days
    ]
    battery_p90 = [
        battery_daily[day].p90_percent if day in battery_daily else None for day in days
    ]
    voltage_p90 = [
        voltage_daily[day].p90_mv if day in voltage_daily else None for day in days
    ]
    temperature_p90 = [
        temperatures[day].p90 if day in temperatures else None for day in days
    ]

    last_seven_days = days[-7:]
    battery_upper = stable_upper_envelope(
        battery_daily[day].p90_percent if day in battery_daily else None
        for day in last_seven_days
    )
    voltage_upper = stable_upper_envelope(
        voltage_daily[day].p90_mv if day in voltage_daily else None
        for day in last_seven_days
    )

    behavior = classify_battery_behavior(battery_median)
    battery_voltage = classify_battery_voltage_relation(
        battery_p90,
        voltage_p90,
    )
    voltage_temperature = (
        classify_temperature_relation(
            voltage_p90,
            temperature_p90,
        )
        if temperatures
        else RelationClassification("unavailable", None, 0)
    )

    return TelemetryProfile(
        battery_behavior=behavior,
        battery_upper_7d_percent=battery_upper,
        voltage_upper_7d_mv=voltage_upper,
        battery_voltage_relation=battery_voltage,
        voltage_temperature_relation=voltage_temperature,
        battery_days=sum(value is not None for value in battery_median),
        voltage_days=sum(value is not None for value in voltage_p90),
        temperature_days=sum(value is not None for value in temperature_p90),
    )
