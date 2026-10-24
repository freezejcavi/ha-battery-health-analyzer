"""Regression tests for conservative MQTT health entity lifecycle cleanup."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from custom_components.battery_health.sensor import (
    BatteryHealthDeviceSensor,
    _orphaned_health_entity_ids,
)


def _entry(
    unique_id: str,
    entity_id: str = "sensor.old_health",
    *,
    domain: str = "sensor",
    platform: str = "battery_health",
) -> SimpleNamespace:
    return SimpleNamespace(
        unique_id=unique_id,
        entity_id=entity_id,
        domain=domain,
        platform=platform,
    )


class EntityLifecycleTests(unittest.TestCase):
    """Remove proven orphans without deleting working or temporarily offline devices."""

    def test_removed_source_is_orphaned_even_when_health_id_was_customized(
        self,
    ) -> None:
        entity = _entry(
            "config123_removed-device_health", "sensor.my_custom_health"
        )
        self.assertEqual(
            _orphaned_health_entity_ids([entity], "config123", set(), set()),
            ["sensor.my_custom_health"],
        )

    def test_current_source_is_retained(self) -> None:
        entity = _entry("config123_device-a_health")
        self.assertEqual(
            _orphaned_health_entity_ids(
                [entity], "config123", {"device-a"}, {"device-a"}
            ),
            [],
        )

    def test_registry_device_is_retained_when_discovery_or_telemetry_is_missing(
        self,
    ) -> None:
        entity = _entry("config123_device-a_health")
        self.assertEqual(
            _orphaned_health_entity_ids(
                [entity], "config123", set(), {"device-a"}
            ),
            [],
        )

    def test_does_not_remove_summary_discovery_or_other_entry(self) -> None:
        entries = [
            _entry("config123_health_summary"),
            _entry("config123_discovered_devices"),
            _entry("another_config_device-a_health"),
            _entry("config123_device-a_health", domain="binary_sensor"),
            _entry("config123_device-b_health", platform="mqtt"),
        ]
        self.assertEqual(
            _orphaned_health_entity_ids(entries, "config123", set(), set()),
            [],
        )

    def test_only_confirmed_orphan_removed_among_multiple_sources(self) -> None:
        entries = [
            _entry("config123_removed_health", "sensor.removed_health"),
            _entry("config123_active_health", "sensor.active_health"),
            _entry("config123_offline_health", "sensor.offline_health"),
        ]
        self.assertEqual(
            _orphaned_health_entity_ids(
                entries, "config123", {"active"}, {"active", "offline"}
            ),
            ["sensor.removed_health"],
        )

    def test_device_name_updates_without_changing_identity(self) -> None:
        sensor = object.__new__(BatteryHealthDeviceSensor)
        sensor._device_id = "device-a"
        sensor._attr_name = "0x1234 Battery health"
        source = SimpleNamespace(device_id="device-a", device_name="Kitchen Window")
        sensor.coordinator = SimpleNamespace(
            data=SimpleNamespace(devices=[source])
        )
        self.assertEqual(sensor.name, "Kitchen Window Battery health")
        source.device_name = "Bathroom Window"
        self.assertEqual(sensor.name, "Bathroom Window Battery health")
        self.assertEqual(sensor._device_id, "device-a")


if __name__ == "__main__":
    unittest.main()
