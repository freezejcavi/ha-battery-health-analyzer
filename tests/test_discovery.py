"""Tests for the pure Battery Health Analyzer discovery engine."""

from __future__ import annotations

import unittest

from custom_components.battery_health.discovery import discover_battery_devices
from custom_components.battery_health.models import EntityDescriptor


def entity(
    entity_id: str,
    *,
    device_id: str | None = "device-1",
    device_class: str | None = None,
    unit: str | None = None,
    disabled: bool = False,
    name: str | None = None,
) -> EntityDescriptor:
    """Create a compact discovery fixture."""
    return EntityDescriptor(
        entity_id=entity_id,
        device_id=device_id,
        domain=entity_id.partition(".")[0],
        device_class=device_class,
        unit=unit,
        disabled=disabled,
        name=name,
    )


class DiscoveryTests(unittest.TestCase):
    """Verify deterministic and conservative source pairing."""

    def test_typical_zigbee_device_is_fully_paired(self) -> None:
        result = discover_battery_devices(
            [
                entity(
                    "sensor.freezer_battery",
                    device_class="battery",
                    unit="%",
                ),
                entity(
                    "sensor.freezer_battery_voltage",
                    device_class="voltage",
                    unit="mV",
                ),
                entity("sensor.freezer_last_seen"),
                entity("sensor.freezer_power_outage_count"),
                entity("sensor.freezer_temperature", device_class="temperature"),
            ],
            {"device-1": "Freezer temperature"},
        )

        self.assertEqual(len(result), 1)
        device = result[0]
        self.assertEqual(device.battery_entity_id, "sensor.freezer_battery")
        self.assertEqual(
            device.voltage_entity_id, "sensor.freezer_battery_voltage"
        )
        self.assertEqual(device.last_seen_entity_id, "sensor.freezer_last_seen")
        self.assertEqual(
            device.outage_entity_id, "sensor.freezer_power_outage_count"
        )
        self.assertEqual(device.issues, ())

    def test_battery_specific_voltage_beats_generic_voltage(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.device_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.device_voltage", device_class="voltage", unit="mV"
                ),
                entity(
                    "sensor.device_battery_voltage",
                    device_class="voltage",
                    unit="mV",
                ),
            ]
        )

        self.assertEqual(
            result[0].voltage_entity_id, "sensor.device_battery_voltage"
        )

    def test_tied_voltage_candidates_are_not_silently_selected(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.device_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.device_a_battery_voltage",
                    device_class="voltage",
                    unit="mV",
                ),
                entity(
                    "sensor.device_b_battery_voltage",
                    device_class="voltage",
                    unit="mV",
                ),
            ]
        )

        self.assertIsNone(result[0].voltage_entity_id)
        self.assertEqual(result[0].issues, ("ambiguous_voltage",))
        self.assertEqual(
            result[0].ambiguous_candidates,
            (
                (
                    "voltage",
                    (
                        "sensor.device_a_battery_voltage",
                        "sensor.device_b_battery_voltage",
                    ),
                ),
            ),
        )
        self.assertEqual(
            result[0].as_dict()["ambiguous_candidates"],
            {
                "voltage": [
                    "sensor.device_a_battery_voltage",
                    "sensor.device_b_battery_voltage",
                ]
            },
        )

    def test_disabled_sources_are_ignored(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.device_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.device_battery_voltage",
                    device_class="voltage",
                    unit="mV",
                    disabled=True,
                ),
            ]
        )

        self.assertIsNone(result[0].voltage_entity_id)
        self.assertEqual(result[0].issues, ("missing_voltage",))

    def test_voltage_is_discovered_without_live_state_metadata(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.device_battery", device_class="battery", unit="%"),
                entity("sensor.device_voltage"),
            ]
        )

        self.assertEqual(result[0].voltage_entity_id, "sensor.device_voltage")
        self.assertEqual(result[0].issues, ())

    def test_mains_voltage_is_not_used_as_battery_voltage(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.device_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.device_mains_voltage",
                    device_class="voltage",
                    unit="V",
                ),
            ]
        )

        self.assertIsNone(result[0].voltage_entity_id)
        self.assertEqual(result[0].issues, ("missing_voltage",))

    def test_actuator_voltage_sensors_are_not_battery_candidates(self) -> None:
        result = discover_battery_devices(
            [
                entity("sensor.radiator_battery", device_class="battery", unit="%"),
                entity(
                    "sensor.radiator_valve_closing_limit_voltage",
                    device_class="voltage",
                    unit="V",
                ),
                entity(
                    "sensor.radiator_valve_motor_running_voltage",
                    device_class="voltage",
                    unit="V",
                ),
                entity(
                    "sensor.radiator_valve_opening_limit_voltage",
                    device_class="voltage",
                    unit="V",
                ),
            ]
        )

        self.assertIsNone(result[0].voltage_entity_id)
        self.assertEqual(result[0].issues, ("missing_voltage",))
        self.assertEqual(result[0].ambiguous_candidates, ())

    def test_binary_low_battery_only_device_is_discovered(self) -> None:
        result = discover_battery_devices(
            [
                entity(
                    "binary_sensor.remote_battery_low",
                    device_class="battery",
                )
            ]
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].battery_low_entity_id,
            "binary_sensor.remote_battery_low",
        )
        self.assertEqual(
            result[0].issues,
            ("missing_battery_percentage", "missing_voltage"),
        )

    def test_device_less_battery_entity_is_not_discovered(self) -> None:
        result = discover_battery_devices(
            [
                entity(
                    "sensor.orphan_battery",
                    device_id=None,
                    device_class="battery",
                    unit="%",
                )
            ]
        )

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
