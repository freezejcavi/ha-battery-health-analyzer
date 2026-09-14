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

from .const import DOMAIN, NAME, SOURCE_PLATFORM
from .coordinator import BatteryHealthCoordinator
from .cycle import assess_cycle_integrity
from .evidence import build_evidence_model, classify_voltage_information


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the telemetry diagnostic sensor."""
    async_add_entities([BatteryHealthDiscoverySensor(entry, entry.runtime_data)])


class BatteryHealthDiscoverySensor(
    CoordinatorEntity[BatteryHealthCoordinator], SensorEntity
):
    """Expose read-only discovery and telemetry profiling diagnostics."""

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
        """Return the number of discovered MQTT battery devices."""
        return len(self.coordinator.data.devices)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return compact read-only telemetry diagnostics."""
        snapshot = self.coordinator.data
        operability = self.coordinator.operability
        devices = snapshot.devices
        device_diagnostics: list[dict[str, Any]] = []
        evidence_readiness = {"ready": 0, "limited": 0, "blocked": 0}
        cycle_integrity_counts = {
            "stable": 0,
            "possible_boundary": 0,
            "probable_boundary": 0,
            "insufficient": 0,
        }

        for device in devices:
            diagnostics = device.as_dict()
            diagnostics["battery_percent_now"] = snapshot.battery_percent.get(
                device.device_id
            )
            diagnostics["battery_percent_source"] = (
                snapshot.battery_percent_source.get(device.device_id)
            )

            battery_history = (
                snapshot.battery_history.get(device.battery_entity_id)
                if device.battery_entity_id is not None
                else None
            )
            if device.battery_entity_id is not None:
                diagnostics["battery_history"] = (
                    battery_history.as_dict()
                    if battery_history is not None
                    else None
                )

            voltage_history = (
                snapshot.voltage_history.get(device.voltage_entity_id)
                if device.voltage_entity_id is not None
                else None
            )
            if device.voltage_entity_id is not None:
                diagnostics["voltage_history"] = (
                    voltage_history.as_dict()
                    if voltage_history is not None
                    else None
                )

            freshness = (
                operability.freshness.get(device.last_seen_entity_id)
                if device.last_seen_entity_id is not None
                else None
            )
            diagnostics["freshness"] = (
                freshness.as_dict()
                if freshness is not None
                else {
                    "supported": False,
                    "state": "unavailable",
                    "issue": None,
                }
            )

            outage = (
                operability.outages.get(device.outage_entity_id)
                if device.outage_entity_id is not None
                else None
            )
            diagnostics["power_outage"] = (
                outage.as_dict()
                if outage is not None
                else {
                    "supported": False,
                    "events_24h": None,
                    "issue": None,
                }
            )

            learning_result = snapshot.baseline_learning.get(device.device_id)
            if learning_result is not None:
                baseline = learning_result.as_dict()
                baseline["status"] = "provisional"
                baseline["mode"] = "shadow_no_save"
                diagnostics["baseline"] = baseline
            else:
                diagnostics["baseline"] = None

            profile = snapshot.telemetry_profiles.get(device.device_id)
            diagnostics["telemetry_profile"] = (
                profile.as_dict() if profile is not None else None
            )

            battery_daily = {}
            voltage_daily = {}
            long_term = self.coordinator._long_term_history
            if long_term is not None:
                if device.battery_entity_id is not None:
                    battery_daily = long_term.battery_daily.get(
                        device.battery_entity_id,
                        {},
                    )
                if device.voltage_entity_id is not None:
                    voltage_daily = long_term.voltage_daily.get(
                        device.voltage_entity_id,
                        {},
                    )
            voltage_information = classify_voltage_information(voltage_daily)

            if profile is not None:
                evidence_model = build_evidence_model(
                    profile,
                    battery_history,
                    voltage_history,
                    freshness,
                    outage,
                    voltage_information,
                )
                diagnostics["evidence_model"] = evidence_model.as_dict()
                if evidence_model.decision_readiness in evidence_readiness:
                    evidence_readiness[evidence_model.decision_readiness] += 1
            else:
                diagnostics["evidence_model"] = None

            cycle_integrity = assess_cycle_integrity(
                battery_daily,
                voltage_daily,
                battery_history,
                voltage_history,
                voltage_information,
                freshness.state if freshness is not None else None,
            )
            diagnostics["cycle_integrity"] = cycle_integrity.as_dict()
            if cycle_integrity.state in cycle_integrity_counts:
                cycle_integrity_counts[cycle_integrity.state] += 1

            device_diagnostics.append(diagnostics)

        freshness_states = (
            "fresh",
            "late",
            "stale",
            "insufficient",
            "invalid",
            "unavailable",
        )
        freshness_state_counts = {
            state: sum(
                evidence.state == state
                for evidence in operability.freshness.values()
            )
            for state in freshness_states
        }

        return {
            "scope": "mqtt_integration_only",
            "source_platform": SOURCE_PLATFORM,
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
            "baseline_available": sum(
                result.record is not None
                for result in snapshot.baseline_learning.values()
            ),
            "profiles_available": len(snapshot.telemetry_profiles),
            "freshness_supported": len(operability.freshness),
            "freshness_states": freshness_state_counts,
            "power_outage_supported": len(operability.outages),
            "power_outage_events_24h_total": sum(
                evidence.events_24h or 0
                for evidence in operability.outages.values()
            ),
            "evidence_readiness": evidence_readiness,
            "cycle_integrity": cycle_integrity_counts,
            "devices": device_diagnostics,
        }
