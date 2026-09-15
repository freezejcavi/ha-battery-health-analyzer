"""Tests for pure baseline-learning policy."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.battery_health.baseline import (
    baseline_confidence,
    learn_baseline,
    parse_battery_percent,
    select_battery_percent,
)
from custom_components.battery_health.models import (
    BaselineRecord,
    VoltageHistorySummary,
)

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def history(median_mv: float = 3000, coverage: float = 1) -> VoltageHistorySummary:
    """Create a compact valid history summary."""
    return VoltageHistorySummary(
        median_mv=median_mv,
        coverage_ratio=coverage,
        valid_duration_seconds=coverage * 24 * 3600,
        source_points=20,
        latest_voltage_mv=median_mv,
    )


def record(
    baseline_mv: float = 3000,
    age_hours: int = 0,
    last_qualified_hours_ago: int = 0,
) -> BaselineRecord:
    """Create an existing baseline record."""
    first = NOW - timedelta(hours=age_hours)
    last = NOW - timedelta(hours=last_qualified_hours_ago)
    return BaselineRecord(baseline_mv, first, last, 1)


class BaselineLearningTests(unittest.TestCase):
    """Verify conservative and monotonic baseline learning."""

    def test_percentage_parser_rejects_invalid_values(self) -> None:
        self.assertEqual(parse_battery_percent("80"), 80)
        self.assertIsNone(parse_battery_percent("unknown"))
        self.assertIsNone(parse_battery_percent(101))

    def test_live_percentage_has_priority_over_recorder(self) -> None:
        self.assertEqual(
            select_battery_percent("85", [70, 80]),
            (85, "current"),
        )

    def test_recorder_percentage_is_used_when_live_state_is_absent(self) -> None:
        self.assertEqual(
            select_battery_percent(None, [70, 80]),
            (80, "recorder"),
        )

    def test_unavailable_live_state_blocks_recorder_fallback(self) -> None:
        self.assertEqual(
            select_battery_percent("unavailable", [70, 80]),
            (None, "unavailable"),
        )

    def test_trailing_unavailable_recorder_state_is_not_skipped(self) -> None:
        self.assertEqual(
            select_battery_percent(None, [80, "unavailable"]),
            (None, "unavailable"),
        )

    def test_starts_baseline_from_healthy_well_covered_window(self) -> None:
        result = learn_baseline(None, history(3050), 90, NOW)

        self.assertTrue(result.changed)
        self.assertEqual(result.state, "baseline_started")
        self.assertEqual(result.record.baseline_mv, 3050)
        self.assertEqual(result.confidence, 0.5)

    def test_low_battery_never_bootstraps_baseline(self) -> None:
        result = learn_baseline(None, history(2600), 20, NOW)

        self.assertFalse(result.changed)
        self.assertIsNone(result.record)
        self.assertEqual(result.state, "battery_not_healthy")

    def test_insufficient_coverage_never_bootstraps_baseline(self) -> None:
        result = learn_baseline(None, history(3050, 0.5), 100, NOW)

        self.assertIsNone(result.record)
        self.assertEqual(result.state, "insufficient_coverage")

    def test_existing_baseline_never_decreases(self) -> None:
        existing = record(3000, age_hours=6, last_qualified_hours_ago=6)
        result = learn_baseline(existing, history(2800), 90, NOW)

        self.assertEqual(result.record.baseline_mv, 3000)
        self.assertEqual(result.state, "baseline_observed")

    def test_material_higher_median_raises_baseline(self) -> None:
        existing = record(3000)
        result = learn_baseline(existing, history(3060), 90, NOW)

        self.assertTrue(result.changed)
        self.assertEqual(result.record.baseline_mv, 3060)
        self.assertEqual(result.state, "baseline_raised")

    def test_confidence_reaches_one_after_48_hours(self) -> None:
        existing = record(3000, age_hours=48)

        self.assertEqual(baseline_confidence(existing), 1)

    def test_confidence_uses_only_qualified_observation_span(self) -> None:
        existing = record(
            3000,
            age_hours=48,
            last_qualified_hours_ago=24,
        )

        self.assertEqual(baseline_confidence(existing), 0.75)

    def test_storage_round_trip_preserves_record(self) -> None:
        existing = record(3000, age_hours=24)

        restored = BaselineRecord.from_storage_dict(existing.as_storage_dict())

        self.assertEqual(restored, existing)


if __name__ == "__main__":
    unittest.main()
