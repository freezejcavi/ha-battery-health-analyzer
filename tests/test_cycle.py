"""Tests for conservative battery-cycle integrity detection."""

from __future__ import annotations

import unittest

from custom_components.battery_health.cycle import assess_cycle_integrity
from custom_components.battery_health.evidence import VoltageInformation
from custom_components.battery_health.models import (
    BatteryHistorySummary,
    VoltageHistorySummary,
)


def battery_summary(
    p50: float,
    *,
    p90: float | None = None,
    value_range: float = 0,
) -> BatteryHistorySummary:
    p90 = p50 if p90 is None else p90
    return BatteryHistorySummary(
        p10_percent=p50,
        median_percent=p50,
        p90_percent=p90,
        min_percent=p50,
        max_percent=p50 + value_range,
        range_percent=value_range,
        coverage_ratio=1,
        valid_duration_seconds=86400,
        history_rows=2,
        value_changes=1,
        latest_percent=p50,
    )


def voltage_summary(
    p50: float,
    *,
    p10: float | None = None,
    p90: float | None = None,
) -> VoltageHistorySummary:
    p10 = p50 if p10 is None else p10
    p90 = p50 if p90 is None else p90
    return VoltageHistorySummary(
        median_mv=p50,
        coverage_ratio=1,
        valid_duration_seconds=86400,
        source_points=2,
        latest_voltage_mv=p50,
        p10_mv=p10,
        p90_mv=p90,
        min_mv=p10,
        max_mv=p90,
        range_mv=p90 - p10,
        value_changes=1,
    )


def daily_battery(value: float, days: int = 7) -> dict[str, BatteryHistorySummary]:
    return {
        f"2026-09-{day:02d}": battery_summary(value, p90=value)
        for day in range(1, days + 1)
    }


def daily_voltage(value: float, days: int = 7) -> dict[str, VoltageHistorySummary]:
    return {
        f"2026-09-{day:02d}": voltage_summary(value, p90=value)
        for day in range(1, days + 1)
    }


def voltage_information(kind: str = "continuous") -> VoltageInformation:
    return VoltageInformation(
        information=kind,
        confidence=1,
        valid_days=30,
        distinct_levels=20 if kind == "continuous" else 2,
        span_mv=300,
        min_step_mv=1 if kind == "continuous" else 100,
    )


class CycleIntegrityTests(unittest.TestCase):
    """Verify cycle-boundary gating without producing a health verdict."""

    def test_joint_persistent_upshift_is_probable_boundary(self) -> None:
        result = assess_cycle_integrity(
            daily_battery(65),
            daily_voltage(2948),
            battery_summary(100, value_range=0),
            voltage_summary(3237, p10=3119, p90=3250),
            voltage_information(),
            "fresh",
        )

        self.assertEqual(result.state, "probable_boundary")
        self.assertFalse(result.history_usable)
        self.assertEqual(result.battery_signal, "persistent_upshift")
        self.assertEqual(result.voltage_signal, "persistent_upshift")
        self.assertAlmostEqual(result.battery_upshift_pp or 0, 35)
        self.assertAlmostEqual(result.voltage_upshift_p50_mv or 0, 289)

    def test_normal_current_level_is_stable(self) -> None:
        result = assess_cycle_integrity(
            daily_battery(100),
            daily_voltage(3044),
            battery_summary(100),
            voltage_summary(3039, p10=3038, p90=3040),
            voltage_information(),
            "fresh",
        )

        self.assertEqual(result.state, "stable")
        self.assertTrue(result.history_usable)

    def test_battery_only_large_upshift_is_possible_boundary(self) -> None:
        result = assess_cycle_integrity(
            daily_battery(40),
            {},
            battery_summary(90, value_range=0),
            None,
            voltage_information("insufficient"),
            "fresh",
        )

        self.assertEqual(result.state, "possible_boundary")
        self.assertFalse(result.history_usable)
        self.assertEqual(result.reasons, ("battery_only_large_upshift",))

    def test_volatile_battery_recovery_is_not_cycle_boundary(self) -> None:
        result = assess_cycle_integrity(
            daily_battery(40),
            {},
            battery_summary(80, value_range=45),
            None,
            voltage_information("insufficient"),
            "fresh",
        )

        self.assertEqual(result.state, "stable")
        self.assertTrue(result.history_usable)
        self.assertEqual(result.battery_signal, "normal")

    def test_static_voltage_is_not_cycle_evidence(self) -> None:
        result = assess_cycle_integrity(
            daily_battery(100),
            daily_voltage(2600),
            battery_summary(100),
            voltage_summary(3000, p10=3000, p90=3000),
            voltage_information("static"),
            "fresh",
        )

        self.assertEqual(result.state, "stable")
        self.assertEqual(result.voltage_signal, "static")

    def test_untrusted_freshness_blocks_cycle_assessment(self) -> None:
        result = assess_cycle_integrity(
            daily_battery(65),
            daily_voltage(2948),
            battery_summary(100),
            voltage_summary(3237, p10=3119, p90=3250),
            voltage_information(),
            "insufficient",
        )

        self.assertEqual(result.state, "insufficient")
        self.assertFalse(result.history_usable)
        self.assertEqual(result.reasons, ("freshness_untrusted",))

    def test_reference_requires_three_valid_days(self) -> None:
        result = assess_cycle_integrity(
            daily_battery(65, days=2),
            daily_voltage(2948, days=2),
            battery_summary(100),
            voltage_summary(3237, p10=3119, p90=3250),
            voltage_information(),
            "fresh",
        )

        self.assertEqual(result.state, "insufficient")
        self.assertFalse(result.history_usable)


if __name__ == "__main__":
    unittest.main()
