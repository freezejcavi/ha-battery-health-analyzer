"""Tests for pure evidence routing."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from custom_components.battery_health.evidence import (
    VoltageInformation,
    build_evidence_model,
    classify_voltage_information,
)
from custom_components.battery_health.models import (
    BatteryHistorySummary,
    BehaviorClassification,
    RelationClassification,
    TelemetryProfile,
    VoltageHistorySummary,
)
from custom_components.battery_health.operability import (
    FreshnessEvidence,
    OutageEvidence,
)


def battery_summary(value: float = 50) -> BatteryHistorySummary:
    return BatteryHistorySummary(
        p10_percent=value,
        median_percent=value,
        p90_percent=value,
        min_percent=value,
        max_percent=value,
        range_percent=0,
        coverage_ratio=1,
        valid_duration_seconds=86400,
        history_rows=2,
        value_changes=0,
        latest_percent=value,
    )


def voltage_summary(
    median: float,
    *,
    p10: float | None = None,
    p90: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> VoltageHistorySummary:
    p10 = median if p10 is None else p10
    p90 = median if p90 is None else p90
    minimum = p10 if minimum is None else minimum
    maximum = p90 if maximum is None else maximum
    return VoltageHistorySummary(
        median_mv=median,
        coverage_ratio=1,
        valid_duration_seconds=86400,
        source_points=2,
        latest_voltage_mv=median,
        p10_mv=p10,
        p90_mv=p90,
        min_mv=minimum,
        max_mv=maximum,
        range_mv=maximum - minimum,
        value_changes=1,
    )


def profile(
    behavior: str = "static",
    battery_voltage: str = "weak",
    temperature: str = "unavailable",
) -> TelemetryProfile:
    return TelemetryProfile(
        battery_behavior=BehaviorClassification(behavior, 1, 30),
        battery_upper_7d_percent=50,
        voltage_upper_7d_mv=3000,
        battery_voltage_relation=RelationClassification(battery_voltage, 0.2, 30),
        voltage_temperature_relation=RelationClassification(temperature, None, 0),
        battery_days=30,
        voltage_days=30,
        temperature_days=0,
    )


def freshness(state: str) -> FreshnessEvidence:
    now = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    return FreshnessEvidence(
        supported=True,
        last_seen=now,
        source="current",
        age_seconds=0,
        reports_24h=10,
        cadence_samples=9,
        median_gap_seconds=300,
        p90_gap_seconds=600,
        age_to_p90_ratio=0,
        state=state,
    )


class VoltageInformationTests(unittest.TestCase):
    def test_static_voltage_is_detected(self) -> None:
        daily = {f"d{day}": voltage_summary(2600) for day in range(1, 31)}
        result = classify_voltage_information(daily)
        self.assertEqual(result.information, "static")
        self.assertEqual(result.distinct_levels, 1)

    def test_quantized_voltage_is_detected(self) -> None:
        daily = {
            f"d{day}": voltage_summary(2700 if day < 16 else 2800)
            for day in range(1, 31)
        }
        result = classify_voltage_information(daily)
        self.assertEqual(result.information, "quantized")
        self.assertGreaterEqual(result.min_step_mv or 0, 25)

    def test_continuous_voltage_is_detected(self) -> None:
        daily = {
            f"d{day}": voltage_summary(
                2900 + day,
                p10=2898 + day,
                p90=2903 + day,
            )
            for day in range(1, 31)
        }
        result = classify_voltage_information(daily)
        self.assertEqual(result.information, "continuous")
        self.assertGreater(result.distinct_levels, 6)

    def test_short_voltage_history_is_insufficient(self) -> None:
        daily = {f"d{day}": voltage_summary(3000) for day in range(1, 5)}
        result = classify_voltage_information(daily)
        self.assertEqual(result.information, "insufficient")


class EvidenceRoutingTests(unittest.TestCase):
    def test_derived_volatile_pair_is_shared_and_deduplicated(self) -> None:
        result = build_evidence_model(
            profile("volatile", "derived", "weak_positive"),
            battery_summary(10),
            voltage_summary(2850),
            freshness("fresh"),
            None,
            VoltageInformation("continuous", 1, 30, 20, 120, 2),
        )
        self.assertEqual(result.battery_role, "shared")
        self.assertEqual(result.voltage_role, "shared")
        self.assertEqual(result.battery_processing, "upper_envelope")
        self.assertTrue(result.double_count_guard)
        self.assertEqual(result.independent_condition_channels, 1)
        self.assertEqual(result.decision_readiness, "ready")

    def test_static_voltage_becomes_context_only(self) -> None:
        result = build_evidence_model(
            profile("monotonic", "insufficient"),
            battery_summary(20),
            voltage_summary(2600),
            freshness("fresh"),
            None,
            VoltageInformation("static", 1, 30, 1, 0, None),
        )
        self.assertEqual(result.battery_role, "primary")
        self.assertEqual(result.battery_processing, "trend")
        self.assertEqual(result.voltage_role, "context_only")
        self.assertEqual(result.independent_condition_channels, 1)
        self.assertIn("voltage_static", result.limitations)

    def test_battery_only_device_can_be_ready_with_one_channel(self) -> None:
        result = build_evidence_model(
            profile("static", "insufficient"),
            battery_summary(86),
            None,
            freshness("fresh"),
            None,
            VoltageInformation("insufficient", 0, 0, 0, None, None),
        )
        self.assertEqual(result.voltage_role, "unavailable")
        self.assertEqual(result.independent_condition_channels, 1)
        self.assertEqual(result.decision_readiness, "ready")

    def test_insufficient_freshness_limits_decision(self) -> None:
        result = build_evidence_model(
            profile("monotonic", "insufficient"),
            battery_summary(20),
            voltage_summary(2600),
            freshness("insufficient"),
            None,
            VoltageInformation("static", 1, 30, 1, 0, None),
        )
        self.assertEqual(result.freshness_gate, "limited")
        self.assertEqual(result.decision_readiness, "limited")

    def test_stale_freshness_blocks_decision(self) -> None:
        result = build_evidence_model(
            profile(),
            battery_summary(),
            voltage_summary(3000),
            freshness("stale"),
            None,
            VoltageInformation("continuous", 1, 30, 20, 50, 1),
        )
        self.assertEqual(result.freshness_gate, "blocked")
        self.assertEqual(result.decision_readiness, "blocked")

    def test_outage_events_are_supporting_escalation_only(self) -> None:
        outage = OutageEvidence(
            supported=True,
            latest_count=14,
            events_24h=2,
            increment_transitions_24h=2,
            resets_24h=0,
            history_rows=5,
            valid_rows=5,
        )
        result = build_evidence_model(
            profile(),
            battery_summary(),
            voltage_summary(3000),
            freshness("fresh"),
            outage,
            VoltageInformation("continuous", 1, 30, 20, 50, 1),
        )
        self.assertEqual(result.outage_role, "escalating_support")
        self.assertEqual(result.decision_readiness, "ready")


if __name__ == "__main__":
    unittest.main()
