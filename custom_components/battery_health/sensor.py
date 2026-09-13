"""Diagnostic sensor platform for Battery Health Analyzer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import BatteryHealthCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the discovery diagnostic sensor."""
    async_add_entities([BatteryHealthDiscoverySensor(entry, entry.runtime_data)])


class BatteryHealthDiscoverySensor(
    CoordinatorEntity[BatteryHealthCoordinator], SensorEntity
):
    """Expose the read-only discovery result for practical validation."""

    _attr_has_entity_name = True
    _attr_name = "Discovered devices"
    _attr_icon = "mdi:battery-search"
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self, entry: ConfigEntry, coordinator: BatteryHealthCoordinator
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_discovered_devices"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
        )

    @property
    def native_value(self) -> int:
        """Return the number of discovered battery devices."""
        return len(self.coordinator.data.devices)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return source pairing details for the read-only PoC."""
        snapshot = self.coordinator.data
        devices = snapshot.devices
        device_diagnostics: list[dict[str, Any]] = []
        for device in devices:
            diagnostics = device.as_dict()
            if device.voltage_entity_id is not None:
                history_summary = snapshot.voltage_history.get(
                    device.voltage_entity_id
                )
                diagnostics["voltage_history"] = (
                    history_summary.as_dict()
                    if history_summary is not None
                    else None
                )
            device_diagnostics.append(diagnostics)

        return {
            "with_voltage": sum(device.has_voltage for device in devices),
            "missing_voltage": sum(
                "missing_voltage" in device.issues for device in devices
            ),
            "ambiguous": sum(device.is_ambiguous for device in devices),
            "history_with_median": sum(
                summary.median_mv is not None
                for summary in snapshot.voltage_history.values()
            ),
            "history_without_median": sum(
                summary.median_mv is None
                for summary in snapshot.voltage_history.values()
            ),
            "devices": device_diagnostics,
        }
