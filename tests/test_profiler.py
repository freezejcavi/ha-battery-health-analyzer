"""Tests for pure telemetry profiling helpers."""

from __future__ import annotations

import unittest

from custom_components.battery_health.models import (
    BatteryHistorySummary,
    NumericHistorySummary,
    VoltageHistorySummary,
)
from custom_components.battery_health.profiler import (
    build_telemetry_profile,
    classify_battery_behavior,
    classify_battery_voltage_relation,
    classify_temperature_relation,
    pearson_correlation,
    stable_upper_envelope,
)


def battery_summary(p50: float, p90: float) -> BatteryHistorySummary:
    """Create one compact daily battery summary."""
    return BatteryHistorySummary(
        p10_percent=p50,
        median_percent=p50,
        p90_percent=p90,
        min_percent=p50,
        max_percent=p90,
        range_percent=p90 - p50,
        coverage_ratio=1,
        valid_duration_seconds=86400,
        history_rows=2,
        value_changes=1,
        latest_percent=p50,
    )


def voltage_summary(p50: float, p90: float) -> VoltageHistorySummary:
    """Create one compact daily voltage summary."""
    return VoltageHistorySummary(
        median_mv=p50,
        coverage_ratio=1,
        valid_duration_seconds=86400,
        source_points=2,
        latest_voltage_mv=p50,
        p10_mv=p50,
        p90_mv=p90,
        min_mv=p50,
        max_mv=p90,
        range_mv=p90 - p50,
        value_changes=1,
    )


def temperature_summary(p90: float) -> NumericHistorySummary:
    """Create one compact daily temperature summary."""
    return NumericHistorySummary(
        p10=p90,
        median=p90,
        p90=p90,
        minimum=p90,
        maximum=p90,
        value_range=0,
        coverage_ratio=1,
        valid_duration_seconds=86400,
        history_rows=1,
        value_changes=0,
        latest=p90,
    )


class UpperEnvelopeTests(unittest.TestCase):
    """Verify robust seven-day upper-envelope behavior."""

    def test_uses_median_of_daily_p90_values(self) -> None:
        self.assertEqual(
            stable_upper_envelope([49, 44, 46, 45, 100, 47, 43]),
            46,
        )

    def test_ignores_missing_values(self) -> None:
        self.assertEqual(stable_upper_envelope([None, 40, None, 50]), 45)
        self.assertIsNone(stable_upper_envelope([None, None]))


class BatteryBehaviorTests(unittest.TestCase):
    """Verify long-term battery reporting classification."""

    def test_static_profile(self) -> None:
        result = classify_battery_behavior([86] * 30)

        self.assertEqual(result.behavior, "static")
        self.assertEqual(result.confidence, 1)
        self.assertEqual(result.valid_days, 30)

    def test_monotonic_profile(self) -> None:
        result = classify_battery_behavior([27, 27, 26, 25, 24, 23, 22, 21, 20])

        self.assertEqual(result.behavior, "monotonic")
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_volatile_profile(self) -> None:
        result = classify_battery_behavior([10, 40, 5, 45, 0, 49, 12, 35])

        self.assertEqual(result.behavior, "volatile")
        self.assertGreaterEqual(result.confidence, 0.8)

    def test_small_non_directional_profile_is_mixed(self) -> None:
        result = classify_battery_behavior([50, 54, 51, 55, 52, 54, 53])

        self.assertEqual(result.behavior, "mixed")

    def test_short_profile_is_insufficient(self) -> None:
        result = classify_battery_behavior([90, 89, 88, 87])

        self.assertEqual(result.behavior, "insufficient")
        self.assertEqual(result.valid_days, 4)


class CorrelationTests(unittest.TestCase):
    """Verify paired telemetry relationship detection."""

    def test_perfect_positive_correlation(self) -> None:
        correlation, paired_days = pearson_correlation(
            [1, 2, 3, 4],
            [10, 20, 30, 40],
        )

        self.assertAlmostEqual(correlation or 0, 1.0)
        self.assertEqual(paired_days, 4)

    def test_constant_signal_has_no_correlation(self) -> None:
        correlation, paired_days = pearson_correlation(
            [100] * 8,
            [3000, 3010, 3020, 3030, 3040, 3050, 3060, 3070],
        )

        self.assertIsNone(correlation)
        self.assertEqual(paired_days, 8)

    def test_battery_voltage_derived_relation(self) -> None:
        result = classify_battery_voltage_relation(
            [5, 10, 15, 20, 25, 30, 35],
            [2750, 2770, 2790, 2810, 2830, 2850, 2870],
        )

        self.assertEqual(result.relation, "derived")
        self.assertAlmostEqual(result.correlation or 0, 1.0)
        self.assertEqual(result.paired_days, 7)

    def test_temperature_relation_keeps_direction(self) -> None:
        result = classify_temperature_relation(
            [2800, 2820, 2840, 2860, 2880, 2900, 2920],
            [-5, 0, 5, 10, 15, 20, 25],
        )

        self.assertEqual(result.relation, "strong_positive")
        self.assertAlmostEqual(result.correlation or 0, 1.0)

    def test_relation_requires_seven_paired_days(self) -> None:
        result = classify_battery_voltage_relation(
            [10, 20, 30, 40, 50, 60],
            [2700, 2750, 2800, 2850, 2900, 2950],
        )

        self.assertEqual(result.relation, "insufficient")
        self.assertEqual(result.paired_days, 6)


class ProfileBuilderTests(unittest.TestCase):
    """Verify aligned daily summaries are converted to one compact profile."""

    def test_builds_envelope_behavior_and_relations(self) -> None:
        days = [f"2026-09-{day:02d}" for day in range(1, 9)]
        battery_daily = {
            day: battery_summary(p50, p90)
            for day, p50, p90 in zip(
                days,
                [10, 40, 5, 45, 0, 49, 12, 35],
                [15, 45, 10, 50, 5, 54, 17, 40],
            )
        }
        voltage_daily = {
            day: voltage_summary(2700 + index * 20, 2750 + index * 20)
            for index, day in enumerate(days)
        }
        temperature_daily = {
            day: temperature_summary(-5 + index * 5)
            for index, day in enumerate(days)
        }

        profile = build_telemetry_profile(
            battery_daily,
            voltage_daily,
            temperature_daily,
        )

        self.assertEqual(profile.battery_behavior.behavior, "volatile")
        self.assertEqual(profile.battery_days, 8)
        self.assertEqual(profile.voltage_days, 8)
        self.assertEqual(profile.temperature_days, 8)
        self.assertIsNotNone(profile.battery_upper_7d_percent)
        self.assertEqual(
            profile.voltage_temperature_relation.relation,
            "strong_positive",
        )

    def test_missing_temperature_source_is_unavailable(self) -> None:
        profile = build_telemetry_profile(
            {"2026-09-01": battery_summary(80, 85)},
            {"2026-09-01": voltage_summary(3000, 3050)},
        )

        self.assertEqual(
            profile.voltage_temperature_relation.relation,
            "unavailable",
        )


if __name__ == "__main__":
    unittest.main()
