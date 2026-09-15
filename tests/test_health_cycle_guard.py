"""Regression test for dev21 old-cycle baseline quarantine."""

from __future__ import annotations

import unittest

from custom_components.battery_health.baseline_v2 import (
    BaselineV2Assessment,
    CycleSegment,
)
from custom_components.battery_health.cycle import CycleIntegrity
from custom_components.battery_health.evidence import EvidenceModel, VoltageInformation
from custom_components.battery_health.health import assess_shadow_health
from custom_components.battery_health.models import (
    BatteryHistorySummary,
    BehaviorClassification,
    RelationClassification,
    TelemetryProfile,
    VoltageHistorySummary,
)


class ShadowHealthCycleGuardTests(unittest.TestCase):
    def test_segmented_learning_does_not_use_previous_cycle_baseline(self) -> None:
        battery = BatteryHistorySummary(
            p10_percent=100,
            median_percent=100,
            p90_percent=100,
            min_percent=100,
            max_percent=100,
            range_percent=0,
            coverage_ratio=1,
            valid_duration_seconds=86400,
            history_rows=4,
            value_changes=0,
            latest_percent=100,
        )
        voltage = VoltageHistorySummary(
            median_mv=3200,
            coverage_ratio=1,
            valid_duration_seconds=86400,
            source_points=4,
            latest_voltage_mv=3200,
            p10_mv=3190,
            p90_mv=3210,
            min_mv=3190,
            max_mv=3210,
            range_mv=20,
            value_changes=2,
        )
        profile = TelemetryProfile(
            battery_behavior=BehaviorClassification("static", 1, 30),
            battery_upper_7d_percent=100,
            voltage_upper_7d_mv=3210,
            battery_voltage_relation=RelationClassification("weak", 0.2, 30),
            voltage_temperature_relation=RelationClassification("unavailable", None, 0),
            battery_days=30,
            voltage_days=30,
            temperature_days=0,
        )
        evidence = EvidenceModel(
            freshness_gate="open",
            decision_readiness="ready",
            battery_role="primary",
            battery_processing="level",
            voltage_role="primary",
            voltage_information=VoltageInformation(
                "continuous", 1, 30, 30, 100, 1
            ),
            battery_voltage_topology="independent",
            temperature_context="unavailable",
            outage_role="neutral",
            independent_condition_channels=2,
            double_count_guard=False,
            limitations=(),
        )
        cycle = CycleIntegrity(
            state="stable",
            history_usable=True,
            battery_reference_percent=100,
            battery_reference_days=7,
            battery_upshift_pp=0,
            battery_signal="normal",
            voltage_reference_mv=3200,
            voltage_reference_days=7,
            voltage_upshift_p50_mv=0,
            voltage_upshift_floor_mv=0,
            voltage_signal="normal",
            reasons=(),
        )
        baseline = BaselineV2Assessment(
            eligibility="learning",
            confidence=0.4,
            candidate_mv=3210,
            candidate_source="cycle_segment_upper_envelope",
            anchor="observed_cycle_boundary",
            voltage_days=2,
            coverage=1,
            cycle_segment=CycleSegment(
                state="segmented",
                boundary_kind="probable_boundary",
                boundary_date="2026-09-15",
                cycle_start_known=False,
                usable_days=("2026-09-16", "2026-09-17"),
                excluded_days=28,
                reasons=("historical_probable_boundary",),
            ),
            limitations=("insufficient_complete_cycle_days",),
        )

        result = assess_shadow_health(
            battery,
            voltage,
            profile,
            evidence,
            cycle,
            baseline,
            persisted_baseline_mv=2900,
            persisted_baseline_confidence=0.65,
        )

        self.assertEqual(result.candidate_state, "unknown")
        self.assertEqual(result.decision_path, "awaiting_cycle_baseline")
        self.assertIn("new_cycle_baseline_not_ready", result.reasons)
        self.assertIn("previous_cycle_baseline_not_usable", result.limitations)


if __name__ == "__main__":
    unittest.main()
