"""Tests for guarded read-only baseline v2."""

from __future__ import annotations

import unittest

from custom_components.battery_health.baseline_v2 import assess_guarded_baseline_v2
from custom_components.battery_health.cycle import CycleIntegrity
from custom_components.battery_health.evidence import EvidenceModel, VoltageInformation
from custom_components.battery_health.models import BatteryHistorySummary, VoltageHistorySummary


def battery(value: float) -> BatteryHistorySummary:
    return BatteryHistorySummary(
        p10_percent=value, median_percent=value, p90_percent=value,
        min_percent=value, max_percent=value, range_percent=0,
        coverage_ratio=1, valid_duration_seconds=86400, history_rows=2,
        value_changes=1, latest_percent=value,
    )


def voltage(value: float, *, p10: float | None = None) -> VoltageHistorySummary:
    p10 = value if p10 is None else p10
    return VoltageHistorySummary(
        median_mv=value, coverage_ratio=1, valid_duration_seconds=86400,
        source_points=2, latest_voltage_mv=value, p10_mv=p10, p90_mv=value,
        min_mv=p10, max_mv=value, range_mv=value - p10, value_changes=1,
    )


def info(kind: str = "continuous") -> VoltageInformation:
    return VoltageInformation(kind, 1.0, 30, 20 if kind == "continuous" else 1, 300, 1)


def evidence(*, voltage_role: str = "primary", readiness: str = "ready") -> EvidenceModel:
    return EvidenceModel(
        freshness_gate="open", decision_readiness=readiness,
        battery_role="primary", battery_processing="level",
        voltage_role=voltage_role, voltage_information=info(),
        battery_voltage_topology="independent", temperature_context="optional",
        outage_role="neutral", independent_condition_channels=2,
        double_count_guard=False,
    )


def integrity(state: str = "stable") -> CycleIntegrity:
    return CycleIntegrity(
        state=state, history_usable=state == "stable",
        battery_reference_percent=65, battery_reference_days=7,
        battery_upshift_pp=35 if state == "probable_boundary" else 0,
        battery_signal="persistent_upshift" if state == "probable_boundary" else "normal",
        voltage_reference_mv=2948, voltage_reference_days=7,
        voltage_upshift_p50_mv=289 if state == "probable_boundary" else 0,
        voltage_upshift_floor_mv=171 if state == "probable_boundary" else 0,
        voltage_signal="persistent_upshift" if state == "probable_boundary" else "normal",
        reasons=("joint_persistent_upshift",) if state == "probable_boundary" else (),
    )


class BaselineV2Tests(unittest.TestCase):
    def test_current_probable_boundary_starts_learning_without_old_history(self) -> None:
        result = assess_guarded_baseline_v2(
            {f"2026-09-{day:02d}": battery(65) for day in range(1, 8)},
            {f"2026-09-{day:02d}": voltage(2948) for day in range(1, 8)},
            voltage(3237, p10=3119), evidence(), integrity("probable_boundary"), info(),
        )
        self.assertEqual(result.eligibility, "learning")
        self.assertEqual(result.cycle_segment.state, "current_boundary")
        self.assertEqual(result.cycle_segment.complete_days, 0)
        self.assertEqual(result.candidate_mv, 3237)
        self.assertLessEqual(result.confidence, 0.4)

    def test_static_voltage_does_not_require_voltage_baseline(self) -> None:
        model = evidence(voltage_role="context_only")
        result = assess_guarded_baseline_v2({}, {}, voltage(2600), model, integrity(), info("static"))
        self.assertEqual(result.eligibility, "not_required")
        self.assertIsNone(result.candidate_mv)

    def test_left_censored_high_battery_can_bootstrap_with_capped_confidence(self) -> None:
        battery_daily = {f"2026-09-{day:02d}": battery(100) for day in range(1, 8)}
        voltage_daily = {f"2026-09-{day:02d}": voltage(3040 + day) for day in range(1, 8)}
        result = assess_guarded_baseline_v2(
            battery_daily, voltage_daily, voltage(3047), evidence(), integrity(), info(),
        )
        self.assertEqual(result.eligibility, "eligible")
        self.assertEqual(result.anchor, "battery_upper_bootstrap")
        self.assertEqual(result.cycle_segment.state, "left_censored")
        self.assertAlmostEqual(result.confidence, 0.65)

    def test_low_battery_without_observed_boundary_stays_learning(self) -> None:
        battery_daily = {f"2026-09-{day:02d}": battery(20) for day in range(1, 8)}
        voltage_daily = {f"2026-09-{day:02d}": voltage(3000 + day) for day in range(1, 8)}
        result = assess_guarded_baseline_v2(
            battery_daily, voltage_daily, voltage(3007), evidence(), integrity(), info(),
        )
        self.assertEqual(result.eligibility, "learning")
        self.assertEqual(result.anchor, "none")
        self.assertIn("healthy_anchor_missing", result.limitations)

    def test_historical_probable_boundary_excludes_old_cycle_and_boundary_day(self) -> None:
        battery_daily = {}
        voltage_daily = {}
        for day in range(1, 8):
            key = f"2026-09-{day:02d}"
            battery_daily[key] = battery(60)
            voltage_daily[key] = voltage(2900)
        for day in range(8, 13):
            key = f"2026-09-{day:02d}"
            battery_daily[key] = battery(100)
            voltage_daily[key] = voltage(3200, p10=3150)
        result = assess_guarded_baseline_v2(
            battery_daily, voltage_daily, voltage(3200, p10=3150), evidence(), integrity(), info(),
        )
        self.assertEqual(result.cycle_segment.state, "segmented")
        self.assertEqual(result.cycle_segment.boundary_date, "2026-09-08")
        self.assertNotIn("2026-09-08", result.cycle_segment.usable_days)
        self.assertEqual(result.eligibility, "eligible")
        self.assertEqual(result.anchor, "observed_cycle_boundary")
        self.assertEqual(result.candidate_mv, 3200)


if __name__ == "__main__":
    unittest.main()
