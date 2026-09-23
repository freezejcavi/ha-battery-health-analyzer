"""Pure tests for the dev24 relative-history Health Model v2."""

from __future__ import annotations

import unittest

from custom_components.battery_health.baseline_v2 import (
    BaselineV2Assessment,
    CycleSegment,
)
from custom_components.battery_health.cycle import CycleIntegrity
from custom_components.battery_health.evidence import EvidenceModel, VoltageInformation
from custom_components.battery_health.health_v2 import assess_relative_health_v2
from custom_components.battery_health.models import (
    BatteryHistorySummary,
    VoltageHistorySummary,
)
from custom_components.battery_health.telemetry_integrity import (
    TelemetryIntegrityAssessment,
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


def voltage_summary(value: float, *, coverage: float = 1.0) -> VoltageHistorySummary:
    return VoltageHistorySummary(
        median_mv=value,
        coverage_ratio=coverage,
        valid_duration_seconds=86400 * coverage,
        source_points=4,
        latest_voltage_mv=value,
        p10_mv=value,
        p90_mv=value,
        min_mv=value,
        max_mv=value,
        range_mv=0,
        value_changes=1,
    )


def battery_daily(values: list[float]) -> dict[str, BatteryHistorySummary]:
    return {
        f"2026-08-{index + 1:02d}": battery_summary(value)
        for index, value in enumerate(values)
    }


def voltage_daily(values: list[float]) -> dict[str, VoltageHistorySummary]:
    return {
        f"2026-08-{index + 1:02d}": voltage_summary(value)
        for index, value in enumerate(values)
    }


def evidence(
    *,
    battery_role: str = "primary",
    battery_processing: str = "level",
    voltage_role: str = "primary",
    voltage_information: str = "continuous",
    topology: str = "unknown",
    temperature_context: str = "optional",
    freshness_gate: str = "open",
    readiness: str = "ready",
) -> EvidenceModel:
    return EvidenceModel(
        freshness_gate=freshness_gate,
        decision_readiness=readiness,
        battery_role=battery_role,
        battery_processing=battery_processing,
        voltage_role=voltage_role,
        voltage_information=VoltageInformation(
            voltage_information,
            1.0 if voltage_information != "insufficient" else 0.0,
            30,
            30,
            200,
            1,
        ),
        battery_voltage_topology=topology,
        temperature_context=temperature_context,
        outage_role="neutral",
        independent_condition_channels=1,
        double_count_guard=True,
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
        voltage_reference_mv=3000,
        voltage_reference_days=7,
        voltage_upshift_p50_mv=0,
        voltage_upshift_floor_mv=0,
        voltage_signal="normal",
        reasons=(),
    )


def baseline(
    *,
    eligibility: str = "learning",
    segment_state: str = "left_censored",
) -> BaselineV2Assessment:
    usable_days = tuple(f"2026-08-{index + 1:02d}" for index in range(30))
    if segment_state == "possible_boundary":
        usable_days = ()
    segment = CycleSegment(
        state=segment_state,
        boundary_kind=(
            "possible_boundary" if segment_state == "possible_boundary" else None
        ),
        boundary_date=("2026-09-12" if segment_state == "possible_boundary" else None),
        cycle_start_known=False,
        usable_days=usable_days,
        excluded_days=0,
        reasons=(),
    )
    return BaselineV2Assessment(
        eligibility=eligibility,
        confidence=0.65 if eligibility == "eligible" else 0.0,
        candidate_mv=3000 if eligibility == "eligible" else None,
        candidate_source=(
            "cycle_segment_upper_envelope" if eligibility == "eligible" else None
        ),
        anchor="battery_upper_bootstrap" if eligibility == "eligible" else "none",
        voltage_days=30 if eligibility == "eligible" else 0,
        coverage=1.0 if eligibility == "eligible" else None,
        cycle_segment=segment,
        limitations=(),
    )


class RelativeVoltageTests(unittest.TestCase):
    def test_p1_style_low_instant_percent_is_ok_when_voltage_history_is_stable(
        self,
    ) -> None:
        daily_voltage = [2910, 2914, 2909, 2908, 2906, 2904, 2905, 2906, 2905, 2904]
        result = assess_relative_health_v2(
            battery_summary(27),
            voltage_summary(2891),
            battery_daily([37] * 10),
            voltage_daily(daily_voltage),
            evidence(
                battery_role="shared",
                battery_processing="upper_envelope",
                voltage_role="shared",
                topology="shared",
            ),
            cycle(),
            baseline(),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "ok")
        self.assertEqual(result.assessment_mode, "relative_voltage")
        self.assertNotEqual(result.calculation_state, "unavailable")
        self.assertGreater(result.ratio_to_reference or 0, 0.98)

    def test_material_but_not_severe_voltage_drop_is_declining(self) -> None:
        result = assess_relative_health_v2(
            battery_summary(3),
            voltage_summary(2855),
            battery_daily([18] * 10),
            voltage_daily([2960, 2966, 2962, 2920, 2900, 2890, 2885, 2880, 2877, 2875]),
            evidence(
                battery_role="shared",
                battery_processing="upper_envelope",
                voltage_role="shared",
                topology="shared",
            ),
            cycle(),
            baseline(),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "declining")

    def test_deep_persistent_voltage_drop_can_reach_replace(self) -> None:
        result = assess_relative_health_v2(
            battery_summary(10),
            voltage_summary(2650),
            battery_daily([80] * 10),
            voltage_daily([3000, 3000, 3000, 2880, 2820, 2780, 2740, 2700, 2680, 2660]),
            evidence(),
            cycle(),
            baseline(),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "replace")


class RelativeBatteryTests(unittest.TestCase):
    def battery_only_evidence(
        self,
        *,
        processing: str = "level",
        temperature_context: str = "optional",
    ) -> EvidenceModel:
        return evidence(
            battery_role="primary",
            battery_processing=processing,
            voltage_role="unavailable",
            voltage_information="insufficient",
            temperature_context=temperature_context,
        )

    def test_static_midrange_battery_percentage_is_ok_not_unknown(self) -> None:
        result = assess_relative_health_v2(
            battery_summary(66),
            None,
            battery_daily([66] * 30),
            {},
            self.battery_only_evidence(),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "ok")
        self.assertEqual(result.assessment_mode, "relative_battery")
        self.assertEqual(result.calculation_state, "ready")

    def test_monotonic_high_percentage_decline_is_declining_not_weakening(self) -> None:
        values = [100, 100, 98, 97, 96, 95, 94, 92, 90, 88, 86, 85]
        result = assess_relative_health_v2(
            battery_summary(85),
            None,
            battery_daily(values),
            {},
            self.battery_only_evidence(processing="trend"),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "declining")

    def test_low_monotonic_percentage_relative_to_own_history_is_weakening(
        self,
    ) -> None:
        values = [27, 27, 26, 26, 25, 24, 24, 23, 22, 21, 21, 20]
        result = assess_relative_health_v2(
            battery_summary(20),
            voltage_summary(2600),
            battery_daily(values),
            voltage_daily([2600] * len(values)),
            self.battery_only_evidence(processing="trend"),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "weakening")

    def test_deep_persistent_battery_only_drop_can_reach_replace(self) -> None:
        values = [100, 100, 95, 80, 60, 40, 20, 14, 12, 10, 9, 8]
        result = assess_relative_health_v2(
            battery_summary(8),
            None,
            battery_daily(values),
            {},
            self.battery_only_evidence(processing="trend"),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "replace")


class RelativeHealthGuardTests(unittest.TestCase):
    def test_required_temperature_can_fall_back_to_independent_battery(self) -> None:
        result = assess_relative_health_v2(
            battery_summary(100),
            voltage_summary(3026),
            battery_daily([100] * 30),
            voltage_daily([3027] * 30),
            evidence(temperature_context="required"),
            cycle(),
            baseline(eligibility="blocked"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "ok")
        self.assertEqual(result.assessment_mode, "relative_battery_temperature_guard")
        self.assertEqual(result.calculation_state, "limited")

    def test_temperature_sensitive_shared_voltage_still_gets_condition(self) -> None:
        result = assess_relative_health_v2(
            battery_summary(48),
            voltage_summary(2922),
            battery_daily([47] * 30),
            voltage_daily([2920, 2921, 2922, 2920, 2922, 2921, 2921] * 4),
            evidence(
                battery_role="shared",
                battery_processing="upper_envelope",
                voltage_role="shared",
                topology="shared",
                temperature_context="required",
            ),
            cycle(),
            baseline(eligibility="blocked"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "ok")
        self.assertEqual(result.assessment_mode, "temperature_guarded_relative_voltage")
        self.assertEqual(result.calculation_state, "limited")

    def test_possible_historical_boundary_limits_confidence_but_not_condition(
        self,
    ) -> None:
        result = assess_relative_health_v2(
            battery_summary(100),
            voltage_summary(3232),
            battery_daily([100] * 30),
            voltage_daily([3243] * 30),
            evidence(voltage_role="supporting", topology="coupled"),
            cycle(),
            baseline(eligibility="blocked", segment_state="possible_boundary"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertEqual(result.condition_state, "ok")
        self.assertEqual(result.calculation_state, "limited")

    def test_service_required_integrity_forces_replace_for_physical_inspection(
        self,
    ) -> None:
        integrity = TelemetryIntegrityAssessment(
            state="service_required",
            battery_trust="rejected",
            voltage_trust="trusted",
            temperature_trust="rejected",
            outage_trust="rejected",
            findings=(
                "battery_abrupt_collapse",
                "temperature_protocol_sentinel",
                "outage_counter_implausible_jump",
            ),
            limitations=("physical_battery_inspection_required",),
        )
        result = assess_relative_health_v2(
            battery_summary(100),
            voltage_summary(3100),
            battery_daily([100] * 30),
            voltage_daily([3100] * 30),
            evidence(),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
            integrity=integrity,
        )
        self.assertEqual(result.condition_state, "replace")
        self.assertEqual(result.calculation_state, "limited")
        self.assertEqual(result.assessment_mode, "telemetry_integrity")
        self.assertIn("telemetry_service_required", result.reasons)

    def test_guarded_integrity_limits_normal_health_calculation(self) -> None:
        integrity = TelemetryIntegrityAssessment(
            state="guarded",
            battery_trust="suspect",
            voltage_trust="trusted",
            temperature_trust="trusted",
            outage_trust="trusted",
            findings=(
                "battery_abrupt_collapse",
                "battery_voltage_contradiction",
            ),
            limitations=("telemetry_integrity_guarded",),
        )
        result = assess_relative_health_v2(
            battery_summary(0),
            voltage_summary(3100),
            battery_daily([100] * 30),
            voltage_daily([3100] * 30),
            evidence(
                voltage_role="context_only",
                voltage_information="static",
            ),
            cycle(),
            baseline(eligibility="not_required"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
            integrity=integrity,
        )
        self.assertEqual(result.calculation_state, "limited")
        self.assertNotEqual(result.condition_state, "replace")
        self.assertIn("telemetry_integrity_guarded", result.limitations)

    def test_no_current_signal_is_measurement_unavailable_not_unknown_condition(
        self,
    ) -> None:
        result = assess_relative_health_v2(
            None,
            None,
            {},
            {},
            self._unavailable_evidence(),
            cycle("insufficient"),
            baseline(eligibility="blocked"),
            persisted_baseline_mv=None,
            persisted_baseline_confidence=None,
        )
        self.assertIsNone(result.condition_state)
        self.assertEqual(result.calculation_state, "unavailable")

    @staticmethod
    def _unavailable_evidence() -> EvidenceModel:
        return evidence(
            battery_role="unavailable",
            battery_processing="limited",
            voltage_role="unavailable",
            voltage_information="insufficient",
            freshness_gate="blocked",
            readiness="blocked",
        )


if __name__ == "__main__":
    unittest.main()
