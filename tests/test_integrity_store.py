"""Tests for persistent telemetry-integrity incident latching."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.battery_health.integrity_store import (
    INTEGRITY_RECOVERY_REPORTS_REQUIRED,
    IntegrityIncidentRecord,
    plan_integrity_incident,
)
from custom_components.battery_health.telemetry_integrity import (
    TelemetryIntegrityAssessment,
)

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def assessment(
    state: str,
    *,
    findings: tuple[str, ...] = (),
) -> TelemetryIntegrityAssessment:
    limitations = (
        ("physical_battery_inspection_required",)
        if state == "service_required"
        else (("telemetry_integrity_guarded",) if state == "guarded" else ())
    )
    return TelemetryIntegrityAssessment(
        state=state,
        battery_trust="rejected" if state == "service_required" else "trusted",
        voltage_trust="trusted",
        temperature_trust="trusted",
        outage_trust="rejected" if findings else "trusted",
        findings=findings,
        limitations=limitations,
    )


class IntegrityIncidentPlanningTests(unittest.TestCase):
    """Verify severe incidents survive windows and clear only on clean reports."""

    def test_service_required_creates_latch(self) -> None:
        raw = assessment(
            "service_required",
            findings=(
                "battery_abrupt_collapse",
                "outage_counter_implausible_jump",
            ),
        )
        result = plan_integrity_incident(None, raw, NOW, NOW)
        self.assertTrue(result.changed)
        self.assertEqual(result.state, "created")
        self.assertIsNotNone(result.record)
        self.assertEqual(result.effective_assessment.state, "service_required")

    def test_same_stale_report_never_advances_recovery(self) -> None:
        created = plan_integrity_incident(
            None,
            assessment("service_required", findings=("battery_abrupt_collapse",)),
            NOW,
            NOW,
        )
        assert created.record is not None

        result = plan_integrity_incident(
            created.record,
            assessment("trusted"),
            NOW,
            NOW + timedelta(hours=6),
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.state, "latched")
        self.assertEqual(result.record.clean_reports, 0)
        self.assertEqual(result.effective_assessment.state, "service_required")

    def test_three_distinct_clean_reports_clear_latch(self) -> None:
        created = plan_integrity_incident(
            None,
            assessment(
                "service_required",
                findings=(
                    "battery_abrupt_collapse",
                    "outage_counter_implausible_jump",
                ),
            ),
            NOW,
            NOW,
        )
        assert created.record is not None
        record = created.record

        historical_only = assessment(
            "guarded",
            findings=(
                "outage_counter_implausible_jump",
                "outage_counter_jump_retained_7d",
            ),
        )

        for index in range(1, INTEGRITY_RECOVERY_REPORTS_REQUIRED):
            step = plan_integrity_incident(
                record,
                historical_only,
                NOW + timedelta(hours=index),
                NOW + timedelta(hours=index),
            )
            self.assertEqual(step.state, "recovery_progress")
            self.assertIsNotNone(step.record)
            self.assertEqual(step.effective_assessment.state, "service_required")
            record = step.record

        cleared = plan_integrity_incident(
            record,
            historical_only,
            NOW + timedelta(hours=INTEGRITY_RECOVERY_REPORTS_REQUIRED),
            NOW + timedelta(hours=INTEGRITY_RECOVERY_REPORTS_REQUIRED),
        )
        self.assertTrue(cleared.changed)
        self.assertEqual(cleared.state, "cleared")
        self.assertIsNone(cleared.record)
        self.assertEqual(cleared.effective_assessment.state, "guarded")

    def test_new_nonclean_report_resets_recovery_streak(self) -> None:
        created = plan_integrity_incident(
            None,
            assessment("service_required", findings=("temperature_protocol_sentinel",)),
            NOW,
            NOW,
        )
        assert created.record is not None

        first_clean = plan_integrity_incident(
            created.record,
            assessment("trusted"),
            NOW + timedelta(hours=1),
            NOW + timedelta(hours=1),
        )
        assert first_clean.record is not None
        self.assertEqual(first_clean.record.clean_reports, 1)

        reset = plan_integrity_incident(
            first_clean.record,
            assessment("guarded", findings=("battery_voltage_contradiction",)),
            NOW + timedelta(hours=2),
            NOW + timedelta(hours=2),
        )
        self.assertEqual(reset.state, "recovery_reset")
        self.assertEqual(reset.record.clean_reports, 0)
        self.assertEqual(reset.effective_assessment.state, "service_required")

    def test_record_roundtrip_preserves_recovery_state(self) -> None:
        record = IntegrityIncidentRecord(
            first_seen=NOW,
            last_bad_at=NOW,
            last_bad_report_at=NOW,
            findings=("outage_counter_implausible_jump",),
            clean_reports=2,
            last_recovery_report_at=NOW + timedelta(hours=2),
        )
        restored = IntegrityIncidentRecord.from_storage_dict(record.as_storage_dict())
        self.assertEqual(restored, record)


if __name__ == "__main__":
    unittest.main()
