"""Tests for cross-signal telemetry integrity decisions."""

from __future__ import annotations

import unittest

from custom_components.battery_health.models import (
    BatteryHistorySummary,
    VoltageHistorySummary,
)
from custom_components.battery_health.operability import OutageEvidence
from custom_components.battery_health.telemetry_integrity import (
    assess_telemetry_integrity,
)


def battery_summary(value: float) -> BatteryHistorySummary:
    return BatteryHistorySummary(
        p10_percent=value,
        median_percent=value,
        p90_percent=value,
        min_percent=value,
        max_percent=value,
        range_percent=0,
        coverage_ratio=1.0,
        valid_duration_seconds=86400,
        history_rows=4,
        value_changes=1,
        latest_percent=value,
    )


def voltage_summary(value: float) -> VoltageHistorySummary:
    return VoltageHistorySummary(
        median_mv=value,
        coverage_ratio=1.0,
        valid_duration_seconds=86400,
        source_points=4,
        latest_voltage_mv=value,
        p10_mv=value,
        p90_mv=value,
        min_mv=value,
        max_mv=value,
        range_mv=0,
        value_changes=1,
    )


def battery_daily(value: float = 100) -> dict[str, BatteryHistorySummary]:
    return {
        f"2026-09-{day:02d}": battery_summary(value)
        for day in range(1, 15)
    }


def voltage_daily(value: float = 3100) -> dict[str, VoltageHistorySummary]:
    return {
        f"2026-09-{day:02d}": voltage_summary(value)
        for day in range(1, 15)
    }


def outage(
    *,
    events: int = 0,
    max_delta: int | None = None,
) -> OutageEvidence:
    return OutageEvidence(
        supported=True,
        latest_count=events,
        events_24h=events,
        increment_transitions_24h=1 if events else 0,
        resets_24h=0,
        history_rows=2,
        valid_rows=2,
        max_positive_delta_24h=max_delta,
    )


class TelemetryIntegrityTests(unittest.TestCase):
    """Verify conservative integrity guards and service escalation."""

    def test_normal_device_is_trusted(self) -> None:
        result = assess_telemetry_integrity(
            battery_summary(100),
            voltage_summary(3100),
            battery_daily(),
            voltage_daily(),
            outage(),
            24.0,
        )
        self.assertEqual(result.state, "trusted")
        self.assertEqual(result.findings, ())

    def test_missing_optional_channels_are_not_penalized(self) -> None:
        result = assess_telemetry_integrity(
            battery_summary(80),
            None,
            battery_daily(80),
            {},
            None,
            None,
        )
        self.assertEqual(result.state, "trusted")
        self.assertEqual(result.voltage_trust, "unavailable")
        self.assertEqual(result.temperature_trust, "unavailable")

    def test_isolated_battery_collapse_with_healthy_voltage_is_guarded(self) -> None:
        result = assess_telemetry_integrity(
            battery_summary(0),
            voltage_summary(3100),
            battery_daily(),
            voltage_daily(),
            outage(),
            24.0,
        )
        self.assertEqual(result.state, "guarded")
        self.assertIn("battery_abrupt_collapse", result.findings)
        self.assertIn("battery_voltage_contradiction", result.findings)
        self.assertEqual(result.battery_trust, "suspect")

    def test_temperature_sentinel_alone_is_guarded(self) -> None:
        result = assess_telemetry_integrity(
            battery_summary(100),
            voltage_summary(3100),
            battery_daily(),
            voltage_daily(),
            outage(),
            -327.7,
        )
        self.assertEqual(result.state, "guarded")
        self.assertEqual(result.temperature_trust, "rejected")

    def test_huge_outage_jump_alone_is_guarded(self) -> None:
        result = assess_telemetry_integrity(
            battery_summary(100),
            voltage_summary(3100),
            battery_daily(),
            voltage_daily(),
            outage(events=26650, max_delta=26650),
            24.0,
        )
        self.assertEqual(result.state, "guarded")
        self.assertEqual(result.outage_trust, "rejected")

    def test_rad_e1_adi_pokoj_corruption_requires_service(self) -> None:
        result = assess_telemetry_integrity(
            battery_summary(0),
            voltage_summary(3100),
            battery_daily(),
            voltage_daily(),
            outage(events=26650, max_delta=26650),
            -327.7,
        )
        self.assertEqual(result.state, "service_required")
        self.assertEqual(result.battery_trust, "rejected")
        self.assertEqual(result.temperature_trust, "rejected")
        self.assertEqual(result.outage_trust, "rejected")
        self.assertIn("battery_abrupt_collapse", result.findings)
        self.assertIn("battery_voltage_contradiction", result.findings)
        self.assertIn("temperature_protocol_sentinel", result.findings)
        self.assertIn("outage_counter_implausible_jump", result.findings)

    def test_two_hard_metadata_failures_require_service(self) -> None:
        result = assess_telemetry_integrity(
            battery_summary(100),
            voltage_summary(3100),
            battery_daily(),
            voltage_daily(),
            outage(events=26650, max_delta=26650),
            -327.7,
        )
        self.assertEqual(result.state, "service_required")


if __name__ == "__main__":
    unittest.main()
