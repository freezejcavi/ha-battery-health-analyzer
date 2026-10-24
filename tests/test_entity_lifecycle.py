"""Regression tests for conservative MQTT health entity lifecycle cleanup."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.battery_health.ha_discovery import async_discover_battery_devices
from custom_components.battery_health.sensor import (
    BatteryHealthDeviceSensor,
    _orphaned_health_entity_ids,
    _registered_device_ids,
    _cleanup_orphaned_health_entities,
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

    def test_ha_device_registry_yields_objects_not_string_ids(self) -> None:
        """Regression: set(registry.devices) used to crash platform setup."""
        registry = SimpleNamespace(
            devices=[
                SimpleNamespace(id="active"),
                SimpleNamespace(id="offline"),
            ]
        )
        self.assertEqual(_registered_device_ids(registry), {"active", "offline"})

    def test_real_registry_cleanup_retains_valid_devices(self) -> None:
        """Exercise the registration adapter rather than prebuilt ID sets."""
        from unittest.mock import Mock

        entries = [
            _entry("config123_removed_health", "sensor.removed_health"),
            _entry("config123_active_health", "sensor.active_health"),
            _entry("config123_offline_health", "sensor.offline_health"),
        ]
        entity_registry = SimpleNamespace(async_remove=Mock())
        device_registry = SimpleNamespace(
            devices=[SimpleNamespace(id="active"), SimpleNamespace(id="offline")]
        )
        with (
            patch(
                "custom_components.battery_health.sensor.er.async_get",
                return_value=entity_registry,
            ),
            patch(
                "custom_components.battery_health.sensor.er.async_entries_for_config_entry",
                return_value=entries,
            ),
            patch(
                "custom_components.battery_health.sensor.dr.async_get",
                return_value=device_registry,
            ),
        ):
            _cleanup_orphaned_health_entities(
                object(),
                SimpleNamespace(entry_id="config123"),
                [SimpleNamespace(device_id="active")],
            )
        entity_registry.async_remove.assert_called_once_with("sensor.removed_health")

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

    def test_mqtt_entity_without_device_registry_record_is_not_discovered(
        self,
    ) -> None:
        mqtt_entry = SimpleNamespace(device_id="deleted", platform="mqtt")
        entities = SimpleNamespace(entities={"sensor.ghost_battery": mqtt_entry})
        devices = SimpleNamespace(async_get=lambda _device_id: None)
        with (
            patch(
                "custom_components.battery_health.ha_discovery.er.async_get",
                return_value=entities,
            ),
            patch(
                "custom_components.battery_health.ha_discovery.dr.async_get",
                return_value=devices,
            ),
        ):
            self.assertEqual(async_discover_battery_devices(object()), [])

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
