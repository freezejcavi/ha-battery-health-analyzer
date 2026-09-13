"""Tests for pure time-weighted voltage statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from custom_components.battery_health.models import VoltageHistoryPoint
from custom_components.battery_health.statistics import (
    ISSUE_NO_RECORDER_HISTORY,
    normalize_voltage_mv,
    summarize_voltage_history,
)


START = datetime(2026, 9, 12, tzinfo=UTC)
END = START + timedelta(hours=24)


def point(hours: float, voltage_mv: float | None) -> VoltageHistoryPoint:
    """Create one history point relative to the test window start."""
    return VoltageHistoryPoint(START + timedelta(hours=hours), voltage_mv)


class VoltageNormalizationTests(unittest.TestCase):
    """Verify explicit V and mV normalization."""

    def test_normalizes_volts_and_millivolts(self) -> None:
        self.assertEqual(normalize_voltage_mv("2.6", "V"), 2600)
        self.assertEqual(normalize_voltage_mv("2600", "mV"), 2600)

    def test_rejects_unknown_values_and_units(self) -> None:
        self.assertIsNone(normalize_voltage_mv("unavailable", "mV"))
        self.assertIsNone(normalize_voltage_mv("2600", None))


class TimeWeightedMedianTests(unittest.TestCase):
    """Verify duration-weighted analysis and coverage."""

    def test_long_healthy_period_outweighs_many_late_samples(self) -> None:
        summary = summarize_voltage_history(
            [
                point(0, 3000),
                point(23, 2500),
                point(23.1, 2550),
                point(23.2, 2500),
                point(23.3, 2550),
            ],
            START,
            END,
        )

        self.assertEqual(summary.median_mv, 3000)
        self.assertEqual(summary.coverage_ratio, 1)

    def test_unavailable_period_reduces_coverage(self) -> None:
        summary = summarize_voltage_history(
            [point(0, 3000), point(6, None), point(18, 2800)],
            START,
            END,
        )

        self.assertEqual(summary.median_mv, 2800)
        self.assertEqual(summary.coverage_ratio, 0.5)
        self.assertEqual(summary.valid_duration_seconds, 12 * 3600)

    def test_first_point_inside_window_leaves_initial_gap(self) -> None:
        summary = summarize_voltage_history(
            [point(6, 2900)],
            START,
            END,
        )

        self.assertEqual(summary.median_mv, 2900)
        self.assertEqual(summary.coverage_ratio, 0.75)

    def test_empty_history_is_explicit(self) -> None:
        summary = summarize_voltage_history([], START, END)

        self.assertIsNone(summary.median_mv)
        self.assertEqual(summary.issue, ISSUE_NO_RECORDER_HISTORY)

    def test_duplicate_timestamp_uses_last_state(self) -> None:
        summary = summarize_voltage_history(
            [point(0, 3000), point(0, 2800)],
            START,
            END,
        )

        self.assertEqual(summary.median_mv, 2800)
        self.assertEqual(summary.source_points, 1)


if __name__ == "__main__":
    unittest.main()
