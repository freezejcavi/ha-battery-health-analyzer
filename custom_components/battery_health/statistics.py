"""Pure voltage-history statistics for Battery Health Analyzer."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .models import VoltageHistoryPoint, VoltageHistorySummary

ISSUE_NO_RECORDER_HISTORY = "no_recorder_history"
ISSUE_NO_VALID_VOLTAGE = "no_valid_voltage"
ISSUE_UNSUPPORTED_VOLTAGE_UNIT = "unsupported_voltage_unit"


def normalize_voltage_mv(value: str | float, unit: str | None) -> float | None:
    """Convert a finite voltage value in V or mV to millivolts."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    if numeric_value != numeric_value or numeric_value in (float("inf"), float("-inf")):
        return None

    normalized_unit = (unit or "").strip().casefold()
    if normalized_unit in {"mv", "millivolt", "millivolts"}:
        return numeric_value
    if normalized_unit in {"v", "volt", "volts"}:
        return numeric_value * 1000
    return None


def summarize_voltage_history(
    points: Iterable[VoltageHistoryPoint],
    window_start: datetime,
    window_end: datetime,
    *,
    unsupported_unit: bool = False,
) -> VoltageHistorySummary:
    """Calculate a time-weighted median and valid-time window coverage."""
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    ordered_points = sorted(points, key=lambda point: point.timestamp)
    if not ordered_points:
        return VoltageHistorySummary(
            median_mv=None,
            coverage_ratio=0,
            valid_duration_seconds=0,
            source_points=0,
            latest_voltage_mv=None,
            issue=(
                ISSUE_UNSUPPORTED_VOLTAGE_UNIT
                if unsupported_unit
                else ISSUE_NO_RECORDER_HISTORY
            ),
        )

    deduplicated: list[VoltageHistoryPoint] = []
    for point in ordered_points:
        if deduplicated and point.timestamp == deduplicated[-1].timestamp:
            deduplicated[-1] = point
        else:
            deduplicated.append(point)

    weighted_values: list[tuple[float, float]] = []
    for index, point in enumerate(deduplicated):
        interval_start = max(point.timestamp, window_start)
        next_timestamp = (
            deduplicated[index + 1].timestamp
            if index + 1 < len(deduplicated)
            else window_end
        )
        interval_end = min(next_timestamp, window_end)
        duration = (interval_end - interval_start).total_seconds()
        if duration > 0 and point.voltage_mv is not None:
            weighted_values.append((point.voltage_mv, duration))

    valid_duration = sum(duration for _, duration in weighted_values)
    window_duration = (window_end - window_start).total_seconds()
    latest_point = next(
        (point for point in reversed(deduplicated) if point.timestamp <= window_end),
        None,
    )

    if not weighted_values:
        return VoltageHistorySummary(
            median_mv=None,
            coverage_ratio=0,
            valid_duration_seconds=0,
            source_points=len(deduplicated),
            latest_voltage_mv=(
                latest_point.voltage_mv if latest_point is not None else None
            ),
            issue=(
                ISSUE_UNSUPPORTED_VOLTAGE_UNIT
                if unsupported_unit
                else ISSUE_NO_VALID_VOLTAGE
            ),
        )

    midpoint = valid_duration / 2
    cumulative_duration = 0.0
    median_mv = weighted_values[0][0]
    for voltage_mv, duration in sorted(weighted_values):
        cumulative_duration += duration
        median_mv = voltage_mv
        if cumulative_duration >= midpoint:
            break

    return VoltageHistorySummary(
        median_mv=median_mv,
        coverage_ratio=min(1.0, valid_duration / window_duration),
        valid_duration_seconds=valid_duration,
        source_points=len(deduplicated),
        latest_voltage_mv=(
            latest_point.voltage_mv if latest_point is not None else None
        ),
        issue=(ISSUE_UNSUPPORTED_VOLTAGE_UNIT if unsupported_unit else None),
    )
