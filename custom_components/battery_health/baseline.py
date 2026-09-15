"""Pure baseline-learning rules for Battery Health Analyzer."""

from __future__ import annotations

from datetime import datetime

from .const import (
    BASELINE_MIN_BATTERY_PERCENT,
    BASELINE_MIN_COVERAGE,
    BASELINE_RAISE_MIN_RATIO,
    BASELINE_SAMPLE_INTERVAL,
)
from .models import (
    BaselineLearningResult,
    BaselineRecord,
    VoltageHistorySummary,
)


def parse_battery_percent(value: str | float | None) -> float | None:
    """Return a finite battery percentage in the physical 0..100 range."""
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if numeric_value != numeric_value or not 0 <= numeric_value <= 100:
        return None
    return numeric_value


def select_battery_percent(
    current_value: str | float | None,
    recorder_values: list[str | float],
) -> tuple[float | None, str]:
    """Use Recorder only when the live state does not exist during startup."""
    if current_value is not None:
        if (current := parse_battery_percent(current_value)) is not None:
            return current, "current"
        return None, "unavailable"
    if (
        recorder_values
        and (recorded := parse_battery_percent(recorder_values[-1])) is not None
    ):
        return recorded, "recorder"
    return None, "unavailable"


def baseline_confidence(record: BaselineRecord) -> float:
    """Return confidence from 0.5 after one 24h window to 1.0 after 48h."""
    qualified_hours = max(
        0.0,
        (record.last_qualified_at - record.first_qualified_at).total_seconds() / 3600,
    )
    return min(1.0, 0.5 + qualified_hours / 96)


def learn_baseline(
    record: BaselineRecord | None,
    history: VoltageHistorySummary | None,
    battery_percent: float | None,
    observed_at: datetime,
) -> BaselineLearningResult:
    """Learn only from well-covered windows with healthy battery evidence."""
    confidence = baseline_confidence(record) if record else 0.0

    if history is None or history.median_mv is None:
        return BaselineLearningResult(record, "missing_history", confidence)
    if history.coverage_ratio < BASELINE_MIN_COVERAGE:
        return BaselineLearningResult(record, "insufficient_coverage", confidence)
    if battery_percent is None:
        return BaselineLearningResult(record, "missing_battery_percent", confidence)
    if battery_percent < BASELINE_MIN_BATTERY_PERCENT:
        return BaselineLearningResult(record, "battery_not_healthy", confidence)

    if record is None:
        new_record = BaselineRecord(
            baseline_mv=history.median_mv,
            first_qualified_at=observed_at,
            last_qualified_at=observed_at,
            sample_count=1,
        )
        return BaselineLearningResult(new_record, "baseline_started", 0.5, True)

    baseline_raised = history.median_mv >= (
        record.baseline_mv * BASELINE_RAISE_MIN_RATIO
    )
    sample_due = observed_at - record.last_qualified_at >= BASELINE_SAMPLE_INTERVAL
    if not baseline_raised and not sample_due:
        return BaselineLearningResult(record, "baseline_preserved", confidence)

    new_record = BaselineRecord(
        baseline_mv=(history.median_mv if baseline_raised else record.baseline_mv),
        first_qualified_at=record.first_qualified_at,
        last_qualified_at=observed_at,
        sample_count=record.sample_count + 1,
        battery_cycle=record.battery_cycle,
    )
    return BaselineLearningResult(
        new_record,
        "baseline_raised" if baseline_raised else "baseline_observed",
        baseline_confidence(new_record),
        True,
    )
