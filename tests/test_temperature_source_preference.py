"""Regression tests for dev22 temperature-source discovery preference."""

from __future__ import annotations

import unittest

from custom_components.battery_health.discovery import discover_battery_devices
from custom_components.battery_health.models import EntityDescriptor


def entity(
    entity_id: str,
    *,
    device_class: str | None = None,
    unit: str | None = None,
) -> EntityDescriptor:
    return EntityDescriptor(
        entity_id=entity_id,
        device_id="device-1",
        domain=entity_id.partition(".")[0],
        device_class=device_class,
        unit=unit,
    )


class TemperatureSourcePreferenceTests(unittest.TestCase):
    def test_regular_sibling_temperature_beats_device_temperature(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.room_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.room_temperature",
                    device_class="temperature",
                    unit="°C",
                ),
                entity(
                    "sensor.room_device_temperature",
                    device_class="temperature",
                    unit="°C",
                ),
            ]
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].temperature_entity_id, "sensor.room_temperature")
        self.assertIsNone(result[0].temperature_issue)
        self.assertNotIn("temperature", dict(result[0].ambiguous_candidates))

    def test_device_temperature_remains_safe_fallback_when_alone(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.room_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.room_device_temperature",
                    device_class="temperature",
                    unit="°C",
                ),
            ]
        )

        self.assertEqual(
            result[0].temperature_entity_id,
            "sensor.room_device_temperature",
        )
        self.assertIsNone(result[0].temperature_issue)

    def test_unrelated_temperature_candidates_stay_ambiguous(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.room_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.room_zone_a_temperature",
                    device_class="temperature",
                    unit="°C",
                ),
                entity(
                    "sensor.room_zone_b_temperature",
                    device_class="temperature",
                    unit="°C",
                ),
            ]
        )

        self.assertIsNone(result[0].temperature_entity_id)
        self.assertEqual(result[0].temperature_issue, "ambiguous_temperature")


if __name__ == "__main__":
    unittest.main()
