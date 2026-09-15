"""Tests for the pure dev21 shadow health assessment."""

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


def battery_summary(value: float, *, coverage: float = 1.0) -> BatteryHistorySummary:
    return BatteryHistorySummary(
        p10_percent=value,
        median_percent=value,
        p90_percent=value,
        min_percent=value,
        max_percent=value,
        range_percent=0,
        coverage_ratio=coverage,
        valid_duration_seconds=86400 * coverage,
        history_rows=4,
        value_changes=1,
        latest_percent=value,
    )


def voltage_summary(p90: float, *, coverage: float = 1.0) -> VoltageHistorySummary:
    return VoltageHistorySummary(
        median_mv=p90,
        coverage_ratio=coverage,
        valid_duration_seconds=86400 * coverage,
        source_points=4,
        latest_voltage_mv=p90,
        p10_mv=p90,
        p90_mv=p90,
        min_mv=p90,
        max_mv=p90,
        range_mv=0,
        value_changes=1,
    )


def profile(
    *,
    behavior: str = "static",
    behavior_confidence: float = 1.0,
    battery_upper: float = 100,
) -> TelemetryProfile:
    return TelemetryProfile(
        battery_behavior=BehaviorClassification(
            behavior,
            behavior_confidence,
            30,
        ),
        battery_upper_7d_percent=battery_upper,
        voltage_upper_7d_mv=3100,
        battery_voltage_relation=RelationClassification("insufficient", None, 30),
        voltage_temperature_relation=RelationClassification("unavailable", None, 0),
        battery_days=30,
        voltage_days=30,
        temperature_days=0,
    )


def evidence(
    *,
    freshness_gate: str = "open",
    readiness: str = "ready",
    battery_role: str = "primary",
    battery_processing: str = "level",
    voltage_role: str = "primary",
    topology: str = "unknown",
    temperature_context: str = "unavailable",
    outage_role: str = "neutral",
    double_count_guard: bool = True,
) -> EvidenceModel:
    return EvidenceModel(
        freshness_gate=freshness_gate,
        decision_readiness=readiness,
        battery_role=battery_role,
        battery_processing=battery_processing,
        voltage_role=voltage_role,
        voltage_information=VoltageInformation(
            "continuous",
            1.0,
            30,
            30,
            100,
            1,
        ),
        battery_voltage_topology=topology,
        temperature_context=temperature_context,
        outage_role=outage_role,
        independent_condition_channels=1,
        double_count_guard=double_count_guard,
        limitations=(),
    )


def cycle(state: str = "stable") -> CycleIntegrity:
    return CycleIntegrity(
        state=state,
        history_usable=state == "stable",
        battery_reference_percent=100,
        battery_reference_days=7,
        battery_upshift_pp=0,
        battery_signal="normal",
        voltage_reference_mv=3100,
        voltage_reference_days=7,
        voltage_upshift_p50_mv=0,
        voltage_upshift_floor_mv=0,
        voltage_signal="normal",
        reasons=(),
    )


def baseline(
    *,
    eligibility: str = "eligible",
    segment_state: str = "left_censored",
) -> BaselineV2Assessment:
    segment = CycleSegment(
        state=segment_state,
        boundary_kind=(
            "possible_boundary" if segment_state == "possible_boundary" else None
        ),
        boundary_date=("2026-09-12" if segment_state == "possible_boundary" else None),
        cycle_start_known=False,
        usable_days=("2026-09-10", "2026-09-11", "2026-09-12"),
        excluded_days=0,
        reasons=(),
    )
    return BaselineV2Assessment(
        eligibility=eligibility,
        confidence=0.65 if eligibility == "eligible" else 0.0,
        candidate_mv=3100 if eligibility == "eligible" else None,
        candidate_source=(
            "cycle_segment_upper_envelope" if eligibility == "eligible" else None
        ),
        anchor="battery_upper_bootstrap" if eligibility == "eligible" else "none",
        voltage_days=30 if eligibility == "eligible" else 0,
        coverage=1.0 if eligibility == "eligible" else None,
        cycle_segment=segment,
        limitations=(),
    )


class ShadowHealthVoltageTests(unittest.TestCase):
    def assess(self, ratio: float):
        return assess_shadow_health(
            battery_summary(100),
            voltage_summary(3100 * ratio),
            profile(),
            evidence(),
            cycle(),
            baseline(),
            persisted_baseline_mv=3100,
            persisted_baseline_confidence=0.65,
        )

    def test_persisted_voltage_baseline_can_score_ok(self) -> None:
        result = self.assess(0.95)
        self.assertEqual(result.candidate_state, "ok")
        self.assertEqual(result.decision_path, "voltage_baseline")
        self.assertAlmostEqual(result.voltage_health_ratio or 0, 0.95)
        self.assertLessEqual(result.confidence, 0.65)

    def test_persisted_voltage_baseline_can_score_weakening(self) -> None:
        result = self.assess(0.89)
        self.assertEqual(result.candidate_state, "weakening")

    def test_persisted_voltage_baseline_can_score_replace(self) -> None:
        result = self.assess(0.85)
        self.assertEqual(result.candidate_state, "replace")

    def test_outage_support_conflicting_with_ok_stays_unknown(self) -> None:
        result = assess_shadow_health(
            battery_summary(100),
            voltage_summary(3000),
            profile(),
            evidence(outage_role="escalating_support"),
            cycle(),
            baseline(),
            persisted_baseline_mv=3100,
            persisted_baseline_confidence=0.65,
        )
        self.assertEqual(result.candidate_state, "unknown")
        self.assertIn("outage_conflicts_with_ok", result.reasons)


class ShadowHealthGuardTests(unittest.TestCase):
    def test_shared_signal_without_persisted_baseline_is_unknown(self) -> None:
        result = assess_shadow_health(
            battery_summary(10),
            voltage_summary(2850),
            profile(behavior="volatile", battery_upper=25),
            evidence(
                battery_role="shared",
                battery_processing="upper_envelope",
                voltage_role="shared",
                topology="shared",
            ),
            cycle(),
            baseline(eligibility="learning"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.candidate_state, "unknown")
        self.assertEqual(result.decision_path, "uncalibrated_shared_signal")

    def test_blocked_evidence_is_unknown(self) -> None:
        result = assess_shadow_health(
            battery_summary(100),
            voltage_summary(3100),
            profile(),
            evidence(freshness_gate="blocked", readiness="blocked"),
            cycle(),
            baseline(),
            persisted_baseline_mv=3100,
            persisted_baseline_confidence=0.65,
        )
        self.assertEqual(result.candidate_state, "unknown")
        self.assertIn("evidence_not_ready", result.reasons)

    def test_cycle_boundary_is_unknown(self) -> None:
        result = assess_shadow_health(
            battery_summary(100),
            voltage_summary(3100),
            profile(),
            evidence(),
            cycle("possible_boundary"),
            baseline(),
            persisted_baseline_mv=3100,
            persisted_baseline_confidence=0.65,
        )
        self.assertEqual(result.candidate_state, "unknown")
        self.assertIn("cycle_integrity_not_stable", result.reasons)

    def test_required_temperature_context_is_unknown(self) -> None:
        result = assess_shadow_health(
            battery_summary(100),
            voltage_summary(3100),
            profile(),
            evidence(temperature_context="required"),
            cycle(),
            baseline(eligibility="blocked"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.candidate_state, "unknown")
        self.assertIn("temperature_context_required", result.reasons)


class ShadowHealthBatteryOnlyTests(unittest.TestCase):
    def battery_only_evidence(self, *, processing: str = "level") -> EvidenceModel:
        return evidence(
            battery_role="primary",
            battery_processing=processing,
            voltage_role="unavailable",
            topology="unknown",
        )

    def test_high_battery_only_signal_can_be_low_confidence_ok(self) -> None:
        result = assess_shadow_health(
            battery_summary(90),
            None,
            profile(behavior="static", battery_upper=90),
            self.battery_only_evidence(),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.candidate_state, "ok")
        self.assertEqual(result.decision_path, "battery_only")
        self.assertLessEqual(result.confidence, 0.55)
        self.assertIn("battery_only_no_replace", result.limitations)

    def test_persistently_low_monotonic_battery_only_signal_is_weakening(self) -> None:
        result = assess_shadow_health(
            battery_summary(20),
            voltage_summary(2600),
            profile(behavior="monotonic", battery_upper=21),
            self.battery_only_evidence(processing="trend"),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.candidate_state, "weakening")
        self.assertNotEqual(result.candidate_state, "replace")
        self.assertIn("battery_only_no_replace", result.limitations)

    def test_midrange_battery_only_signal_stays_unknown(self) -> None:
        result = assess_shadow_health(
            battery_summary(60),
            None,
            profile(behavior="static", battery_upper=60),
            self.battery_only_evidence(),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.candidate_state, "unknown")
        self.assertIn("battery_level_not_calibrated", result.reasons)


if __name__ == "__main__":
    unittest.main()
