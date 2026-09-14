"""Tests for pure telemetry profiling helpers."""

from __future__ import annotations

import unittest

from custom_components.battery_health.profiler import (
    classify_battery_behavior,
    classify_battery_voltage_relation,
    classify_temperature_relation,
    pearson_correlation,
    stable_upper_envelope,
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


if __name__ == "__main__":
    unittest.main()
