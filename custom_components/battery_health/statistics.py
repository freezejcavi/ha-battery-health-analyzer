"""Pure Recorder-history statistics for Battery Health Analyzer."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from .models import (
    BatteryHistoryPoint,
    BatteryHistorySummary,
    NumericHistoryPoint,
    NumericHistorySummary,
    VoltageHistoryPoint,
    VoltageHistorySummary,
)

ISSUE_NO_RECORDER_HISTORY = "no_recorder_history"
ISSUE_NO_VALID_VOLTAGE = "no_valid_voltage"
ISSUE_NO_VALID_BATTERY = "no_valid_battery"
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


def normalize_temperature_c(value: str | float, unit: str | None) -> float | None:
    """Convert a finite temperature value to degrees Celsius."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if numeric_value != numeric_value or numeric_value in (float("inf"), float("-inf")):
        return None

    normalized_unit = (unit or "").strip().casefold()
    if normalized_unit in {"°c", "c", "celsius"}:
        return numeric_value
    if normalized_unit in {"°f", "f", "fahrenheit"}:
        return (numeric_value - 32) * 5 / 9
    return None


def _weighted_percentile(
    weighted_values: Sequence[tuple[float, float]],
    quantile: float,
) -> float:
    """Return one duration-weighted percentile for 0 <= quantile <= 1."""
    total_duration = sum(duration for _, duration in weighted_values)
    threshold = total_duration * quantile
    cumulative = 0.0
    ordered = sorted(weighted_values, key=lambda item: item[0])
    selected = ordered[-1][0]
    for value, duration in ordered:
        cumulative += duration
        selected = value
        if cumulative >= threshold:
            break
    return selected


def _count_value_changes(values: Iterable[float | None]) -> int:
    """Count real numeric value changes while ignoring unavailable gaps."""
    changes = 0
    previous: float | None = None
    has_previous = False
    for value in values:
        if value is None:
            continue
        if has_previous and value != previous:
            changes += 1
        previous = value
        has_previous = True
    return changes


def _weighted_window(
    points: Sequence[tuple[datetime, float | None]],
    window_start: datetime,
    window_end: datetime,
) -> tuple[
    list[tuple[float, float]],
    float,
    float | None,
    int,
    int,
]:
    """Return weighted values, valid duration, latest value and row diagnostics."""
    deduplicated: list[tuple[datetime, float | None]] = []
    for timestamp, value in sorted(points, key=lambda item: item[0]):
        if deduplicated and timestamp == deduplicated[-1][0]:
            deduplicated[-1] = (timestamp, value)
        else:
            deduplicated.append((timestamp, value))

    carry = next(
        (
            point
            for point in reversed(deduplicated)
            if point[0] <= window_start
        ),
        None,
    )
    relevant: list[tuple[datetime, float | None]] = []
    if carry is not None:
        relevant.append(carry)
    relevant.extend(
        point
        for point in deduplicated
        if window_start < point[0] < window_end
    )

    weighted_values: list[tuple[float, float]] = []
    for index, (timestamp, value) in enumerate(relevant):
        interval_start = max(timestamp, window_start)
        next_timestamp = (
            relevant[index + 1][0]
            if index + 1 < len(relevant)
            else window_end
        )
        interval_end = min(next_timestamp, window_end)
        duration = (interval_end - interval_start).total_seconds()
        if duration > 0 and value is not None:
            weighted_values.append((value, duration))

    valid_duration = sum(duration for _, duration in weighted_values)
    latest_value = relevant[-1][1] if relevant else None
    value_changes = _count_value_changes(value for _, value in relevant)
    return (
        weighted_values,
        valid_duration,
        latest_value,
        len(relevant),
        value_changes,
    )


def summarize_voltage_history(
    points: Iterable[VoltageHistoryPoint],
    window_start: datetime,
    window_end: datetime,
    *,
    unsupported_unit: bool = False,
) -> VoltageHistorySummary:
    """Calculate robust time-weighted voltage statistics and coverage."""
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    point_list = list(points)
    if not point_list:
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

    (
        weighted_values,
        valid_duration,
        latest_value,
        history_rows,
        value_changes,
    ) = _weighted_window(
        [(point.timestamp, point.voltage_mv) for point in point_list],
        window_start,
        window_end,
    )
    window_duration = (window_end - window_start).total_seconds()

    if not weighted_values:
        return VoltageHistorySummary(
            median_mv=None,
            coverage_ratio=0,
            valid_duration_seconds=0,
            source_points=history_rows,
            latest_voltage_mv=latest_value,
            issue=(
                ISSUE_UNSUPPORTED_VOLTAGE_UNIT
                if unsupported_unit
                else ISSUE_NO_VALID_VOLTAGE
            ),
            value_changes=value_changes,
        )

    values = [value for value, _ in weighted_values]
    minimum = min(values)
    maximum = max(values)
    return VoltageHistorySummary(
        median_mv=_weighted_percentile(weighted_values, 0.5),
        coverage_ratio=min(1.0, valid_duration / window_duration),
        valid_duration_seconds=valid_duration,
        source_points=history_rows,
        latest_voltage_mv=latest_value,
        issue=(ISSUE_UNSUPPORTED_VOLTAGE_UNIT if unsupported_unit else None),
        p10_mv=_weighted_percentile(weighted_values, 0.1),
        p90_mv=_weighted_percentile(weighted_values, 0.9),
        min_mv=minimum,
        max_mv=maximum,
        range_mv=maximum - minimum,
        value_changes=value_changes,
    )


def summarize_battery_history(
    points: Iterable[BatteryHistoryPoint],
    window_start: datetime,
    window_end: datetime,
) -> BatteryHistorySummary:
    """Calculate robust time-weighted battery-percentage statistics."""
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    point_list = list(points)
    if not point_list:
        return BatteryHistorySummary(
            p10_percent=None,
            median_percent=None,
            p90_percent=None,
            min_percent=None,
            max_percent=None,
            range_percent=None,
            coverage_ratio=0,
            valid_duration_seconds=0,
            history_rows=0,
            value_changes=0,
            latest_percent=None,
            issue=ISSUE_NO_RECORDER_HISTORY,
        )

    (
        weighted_values,
        valid_duration,
        latest_value,
        history_rows,
        value_changes,
    ) = _weighted_window(
        [(point.timestamp, point.battery_percent) for point in point_list],
        window_start,
        window_end,
    )
    window_duration = (window_end - window_start).total_seconds()

    if not weighted_values:
        return BatteryHistorySummary(
            p10_percent=None,
            median_percent=None,
            p90_percent=None,
            min_percent=None,
            max_percent=None,
            range_percent=None,
            coverage_ratio=0,
            valid_duration_seconds=0,
            history_rows=history_rows,
            value_changes=value_changes,
            latest_percent=latest_value,
            issue=ISSUE_NO_VALID_BATTERY,
        )

    values = [value for value, _ in weighted_values]
    minimum = min(values)
    maximum = max(values)
    return BatteryHistorySummary(
        p10_percent=_weighted_percentile(weighted_values, 0.1),
        median_percent=_weighted_percentile(weighted_values, 0.5),
        p90_percent=_weighted_percentile(weighted_values, 0.9),
        min_percent=minimum,
        max_percent=maximum,
        range_percent=maximum - minimum,
        coverage_ratio=min(1.0, valid_duration / window_duration),
        valid_duration_seconds=valid_duration,
        history_rows=history_rows,
        value_changes=value_changes,
        latest_percent=latest_value,
    )


def summarize_numeric_history(
    points: Iterable[NumericHistoryPoint],
    window_start: datetime,
    window_end: datetime,
) -> NumericHistorySummary:
    """Calculate robust time-weighted statistics for a generic numeric signal."""
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    point_list = list(points)
    if not point_list:
        return NumericHistorySummary(
            p10=None,
            median=None,
            p90=None,
            minimum=None,
            maximum=None,
            value_range=None,
            coverage_ratio=0,
            valid_duration_seconds=0,
            history_rows=0,
            value_changes=0,
            latest=None,
            issue=ISSUE_NO_RECORDER_HISTORY,
        )

    (
        weighted_values,
        valid_duration,
        latest_value,
        history_rows,
        value_changes,
    ) = _weighted_window(
        [(point.timestamp, point.value) for point in point_list],
        window_start,
        window_end,
    )
    window_duration = (window_end - window_start).total_seconds()

    if not weighted_values:
        return NumericHistorySummary(
            p10=None,
            median=None,
            p90=None,
            minimum=None,
            maximum=None,
            value_range=None,
            coverage_ratio=0,
            valid_duration_seconds=0,
            history_rows=history_rows,
            value_changes=value_changes,
            latest=latest_value,
            issue="no_valid_numeric_value",
        )

    values = [value for value, _ in weighted_values]
    minimum = min(values)
    maximum = max(values)
    return NumericHistorySummary(
        p10=_weighted_percentile(weighted_values, 0.1),
        median=_weighted_percentile(weighted_values, 0.5),
        p90=_weighted_percentile(weighted_values, 0.9),
        minimum=minimum,
        maximum=maximum,
        value_range=maximum - minimum,
        coverage_ratio=min(1.0, valid_duration / window_duration),
        valid_duration_seconds=valid_duration,
        history_rows=history_rows,
        value_changes=value_changes,
        latest=latest_value,
    )
