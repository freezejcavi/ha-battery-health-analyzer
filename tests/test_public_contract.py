"""Final pre-RC public naming and attribute contract tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from custom_components.battery_health.health_v2 import RelativeHealthAssessment
from custom_components.battery_health.sensor import (
    _canonical_health_entity_id,
    _looks_like_generated_entity_id,
    _public_health_attributes,
)


class PublicContractTests(unittest.TestCase):
    """Keep final entity naming and visible attributes stable and small."""

    def test_health_entity_id_is_derived_from_source_battery_entity(self) -> None:
        self.assertEqual(
            _canonical_health_entity_id("sensor.dvere_balkon_battery", "abcdef"),
            "sensor.dvere_balkon_health",
        )
        self.assertEqual(
            _canonical_health_entity_id("sensor.custom_charge", "abcdef"),
            "sensor.custom_charge_health",
        )

    def test_generated_legacy_id_detection_preserves_custom_ids(self) -> None:
        self.assertTrue(
            _looks_like_generated_entity_id(
                "sensor.network_api_integrations_battery_health_analyzer_dvere_balkon_battery_health",
                "dvere_balkon Battery health",
            )
        )
        self.assertFalse(
            _looks_like_generated_entity_id(
                "sensor.my_custom_door_health",
                "dvere_balkon Battery health",
            )
        )

    def test_voltage_public_attributes_are_small_and_explanatory(self) -> None:
        assessment = RelativeHealthAssessment(
            "declining",
            "ready",
            "falling",
            0.64,
            "relative_voltage",
            "voltage_mv",
            2860.0,
            2900.0,
            2936.0,
            2936.0,
            2860.0 / 2936.0,
            -76.0,
            0.985,
            ("relative_voltage_decline",),
            (),
        )
        attrs = _public_health_attributes(assessment)
        self.assertEqual(attrs["basis"], "voltage")
        self.assertEqual(attrs["current"], 2860)
        self.assertEqual(attrs["reference"], 2936)
        self.assertEqual(attrs["change_unit"], "%")
        self.assertEqual(attrs["reason"], "Early persistent decline from own history")
        self.assertNotIn("assessment_mode", attrs)
        self.assertNotIn("source_device_id", attrs)
        self.assertNotIn("cycle_generation", attrs)
        self.assertNotIn("reasons", attrs)
        self.assertNotIn("limitations", attrs)

    def test_limited_battery_attributes_add_only_readable_note(self) -> None:
        assessment = RelativeHealthAssessment(
            "ok",
            "limited",
            "stable",
            0.5,
            "relative_battery_temperature_guard",
            "battery_percent",
            84.0,
            86.0,
            90.0,
            90.0,
            84.0 / 90.0,
            -6.0,
            0.0,
            ("relative_battery_stable",),
            ("temperature_sensitive_voltage_bypassed",),
        )
        attrs = _public_health_attributes(assessment)
        self.assertEqual(attrs["basis"], "battery_percent")
        self.assertEqual(attrs["change_from_reference"], -6.0)
        self.assertEqual(attrs["change_unit"], "pp")
        self.assertIn("temperature", attrs["note"].lower())

    def test_service_required_replace_explains_physical_inspection(self) -> None:
        assessment = RelativeHealthAssessment(
            "replace",
            "limited",
            "insufficient",
            0.5,
            "telemetry_integrity",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            ("telemetry_service_required",),
            ("physical_battery_inspection_required",),
        )
        attrs = _public_health_attributes(assessment)
        self.assertEqual(attrs["basis"], "telemetry_integrity")
        self.assertEqual(attrs["calculation"], "limited")
        self.assertIn("physical inspection", attrs["reason"].lower())
        self.assertIn("telemetry", attrs["note"].lower())

    def test_deep_discovery_sensor_is_disabled_diagnostic(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components/battery_health/sensor.py"
        ).read_text()
        self.assertIn(
            "_attr_entity_category = EntityCategory.DIAGNOSTIC",
            source,
        )
        self.assertIn(
            "_attr_entity_registry_enabled_default = False",
            source,
        )


if __name__ == "__main__":
    unittest.main()
