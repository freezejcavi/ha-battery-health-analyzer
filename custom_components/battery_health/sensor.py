"""Sensor platform for Battery Health Analyzer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .cadence_store import CADENCE_SAMPLE_INTERVAL_MINUTES
from .const import DOMAIN, NAME, SOURCE_PLATFORM
from .coordinator import BatteryHealthCoordinator
from .health_v2 import (
    BATTERY_DECLINING_DROP_PP,
    BATTERY_LOW_PERCENT,
    BATTERY_REPLACE_7D_PERCENT,
    BATTERY_REPLACE_PERCENT,
    BATTERY_STRONG_DECLINE_DROP_PP,
    BATTERY_WEAKENING_DROP_PP,
    summarize_condition_states,
)
from .health_v2 import (
    CALCULATION_STATES as V2_CALCULATION_STATES,
)
from .health_v2 import (
    CONDITION_STATES as V2_CONDITION_STATES,
)
from .health_v2 import (
    MIN_COVERAGE as V2_MIN_COVERAGE,
)
from .health_v2 import (
    TREND_STATES as V2_TREND_STATES,
)
from .health_v2 import (
    VOLTAGE_DECLINING_RATIO as V2_VOLTAGE_DECLINING_RATIO,
)
from .health_v2 import (
    VOLTAGE_REPLACE_RATIO as V2_VOLTAGE_REPLACE_RATIO,
)
from .health_v2 import (
    VOLTAGE_WEAKENING_RATIO as V2_VOLTAGE_WEAKENING_RATIO,
)

_HEALTH_ICONS = {
    "ok": "mdi:battery-check",
    "declining": "mdi:battery-arrow-down-outline",
    "weakening": "mdi:battery-alert",
    "replace": "mdi:battery-alert-variant-outline",
    "unknown": "mdi:battery-unknown",
}


_LOGGER = logging.getLogger(__name__)

_PUBLIC_REASON = {
    "ok": "Stable relative to own history",
    "declining": "Early persistent decline from own history",
    "weakening": "Material persistent decline from own history",
    "replace": "Deep persistent decline from own history",
}

_LIMITATION_NOTES = {
    "temperature_sensitive_signal_not_normalized": (
        "Temperature affects the voltage comparison"
    ),
    "temperature_sensitive_voltage_bypassed": (
        "Voltage was bypassed because temperature affects it"
    ),
    "cycle_context_limits_escalation": "Battery-cycle context limits escalation",
    "freshness_not_open": "Freshness evidence is limited",
    "evidence_not_fully_ready": "Supporting evidence is limited",
    "low_current_voltage_coverage": "Current voltage history coverage is limited",
    "low_current_battery_coverage": "Current battery history coverage is limited",
    "telemetry_integrity_guarded": (
        "Telemetry is inconsistent and the result is intentionally conservative"
    ),
}


def _canonical_health_entity_id(
    battery_entity_id: str | None,
    device_id: str,
) -> str:
    """Return a short entity ID derived only from the source battery entity."""
    object_id = ""
    if battery_entity_id and "." in battery_entity_id:
        object_id = battery_entity_id.split(".", 1)[1]
    if object_id.endswith("_battery") and len(object_id) > len("_battery"):
        object_id = object_id[: -len("_battery")]
    if not object_id:
        object_id = f"battery_{device_id[:8]}"
    return f"sensor.{object_id}_health"


def _looks_like_generated_entity_id(entity_id: str, legacy_full_name: str) -> bool:
    """Return whether an ID still looks system-generated from the old full name."""
    if "." not in entity_id:
        return False
    object_id = entity_id.split(".", 1)[1]
    legacy_slug = slugify(legacy_full_name)
    padded = f"_{object_id}_"
    token = f"_{legacy_slug}_"
    return (
        object_id == legacy_slug
        or object_id.startswith(f"{legacy_slug}_")
        or object_id.endswith(f"_{legacy_slug}")
        or token in padded
    )


def _rename_registry_entity_if_generated(
    registry: er.EntityRegistry,
    current_entity_id: str | None,
    target_entity_id: str,
    legacy_full_name: str,
) -> bool:
    """Rename only old generated IDs; preserve explicit user custom IDs."""
    if current_entity_id is None or current_entity_id == target_entity_id:
        return False
    if not _looks_like_generated_entity_id(current_entity_id, legacy_full_name):
        return False
    target_entry = registry.async_get(target_entity_id)
    if target_entry is not None:
        _LOGGER.warning(
            "Cannot migrate %s to %s because the target already exists",
            current_entity_id,
            target_entity_id,
        )
        return False
    registry.async_update_entity(current_entity_id, new_entity_id=target_entity_id)
    _LOGGER.info("Migrated entity ID %s -> %s", current_entity_id, target_entity_id)
    return True


def _migrate_health_entity_ids(
    hass: HomeAssistant, entry: ConfigEntry, devices
) -> None:
    """Migrate dev26 generated health IDs to stable source-derived IDs."""
    registry = er.async_get(hass)
    for device in devices:
        unique_id = f"{entry.entry_id}_{device.device_id}_health"
        current_entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        target_entity_id = _canonical_health_entity_id(
            device.battery_entity_id,
            device.device_id,
        )
        display_name = device.device_name or device.device_id[:8]
        _rename_registry_entity_if_generated(
            registry,
            current_entity_id,
            target_entity_id,
            f"{display_name} Battery health",
        )

    summary_unique_id = f"{entry.entry_id}_health_summary"
    summary_current = registry.async_get_entity_id("sensor", DOMAIN, summary_unique_id)
    _rename_registry_entity_if_generated(
        registry,
        summary_current,
        "sensor.battery_health_summary",
        f"{NAME} Summary",
    )


def _public_health_attributes(assessment) -> dict[str, Any]:
    """Return the compact user-facing explanation for one health state."""
    if assessment is None or assessment.condition_state not in V2_CONDITION_STATES:
        return {
            "trend": "insufficient",
            "confidence_percent": 0,
            "calculation": "unavailable",
            "basis": "unavailable",
            "reason": "No usable battery telemetry",
        }

    if assessment.assessment_mode == "telemetry_integrity":
        return {
            "trend": assessment.trend_state,
            "confidence_percent": round(assessment.confidence * 100),
            "calculation": assessment.calculation_state,
            "basis": "telemetry_integrity",
            "reason": "Battery or power telemetry requires physical inspection",
            "note": "Conflicting or implausible device telemetry was detected",
        }

    is_voltage = assessment.signal == "voltage_mv"
    basis = "voltage" if is_voltage else "battery_percent"
    unit = "mV" if is_voltage else "%"
    current = assessment.current_value
    reference = assessment.reference_used
    if is_voltage:
        current_value = round(current) if current is not None else None
        reference_value = round(reference) if reference is not None else None
        change = (
            round((assessment.ratio_to_reference - 1.0) * 100.0, 1)
            if assessment.ratio_to_reference is not None
            else None
        )
        change_unit = "%"
    else:
        current_value = round(current, 1) if current is not None else None
        reference_value = round(reference, 1) if reference is not None else None
        change = (
            round(assessment.delta_from_reference, 1)
            if assessment.delta_from_reference is not None
            else None
        )
        change_unit = "pp"

    attributes: dict[str, Any] = {
        "trend": assessment.trend_state,
        "confidence_percent": round(assessment.confidence * 100),
        "calculation": assessment.calculation_state,
        "basis": basis,
        "current": current_value,
        "reference": reference_value,
        "unit": unit,
        "change_from_reference": change,
        "change_unit": change_unit,
        "reason": _PUBLIC_REASON[assessment.condition_state],
    }
    if assessment.calculation_state == "limited":
        notes = [
            _LIMITATION_NOTES[item]
            for item in assessment.limitations
            if item in _LIMITATION_NOTES
        ]
        if notes:
            attributes["note"] = "; ".join(dict.fromkeys(notes))
    return attributes


def _service_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the integration-owned service device descriptor."""
    return DeviceInfo(
        entry_type=DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up diagnostic, summary and per-device health sensors."""
    coordinator: BatteryHealthCoordinator = entry.runtime_data
    _migrate_health_entity_ids(hass, entry, coordinator.data.devices)
    known_device_ids = {device.device_id for device in coordinator.data.devices}

    async_add_entities(
        [
            BatteryHealthDiscoverySensor(entry, coordinator),
            BatteryHealthSummarySensor(entry, coordinator),
            *[
                BatteryHealthDeviceSensor(
                    entry,
                    coordinator,
                    device.device_id,
                    device.device_name,
                    device.battery_entity_id,
                )
                for device in coordinator.data.devices
            ],
        ]
    )

    @callback
    def _add_new_health_entities() -> None:
        """Create entities for MQTT battery devices discovered after setup."""
        new_entities: list[BatteryHealthDeviceSensor] = []
        for device in coordinator.data.devices:
            if device.device_id in known_device_ids:
                continue
            known_device_ids.add(device.device_id)
            new_entities.append(
                BatteryHealthDeviceSensor(
                    entry,
                    coordinator,
                    device.device_id,
                    device.device_name,
                    device.battery_entity_id,
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_health_entities))


class BatteryHealthDeviceSensor(
    CoordinatorEntity[BatteryHealthCoordinator], SensorEntity
):
    """Publish the validated health state for one discovered MQTT battery device."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(V2_CONDITION_STATES)
    _attr_has_entity_name = False
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: BatteryHealthCoordinator,
        device_id: str,
        device_name: str | None,
        battery_entity_id: str | None,
    ) -> None:
        """Initialize one stable per-device health entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        display_name = device_name or device_id[:8]
        self._attr_name = f"{display_name} Battery health"
        self.entity_id = _canonical_health_entity_id(battery_entity_id, device_id)
        self._attr_unique_id = f"{entry.entry_id}_{device_id}_health"
        self._attr_device_info = _service_device_info(entry)

    def _device(self):
        """Return the currently discovered device descriptor, if still present."""
        return next(
            (
                device
                for device in self.coordinator.data.devices
                if device.device_id == self._device_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        """Expose HA unavailable only when the v2 condition cannot be calculated."""
        assessment = self.coordinator.health_v2_assessments.get(self._device_id)
        return (
            super().available
            and assessment is not None
            and assessment.calculation_state != "unavailable"
            and assessment.condition_state in V2_CONDITION_STATES
        )

    @property
    def native_value(self) -> str | None:
        """Return the promoted Health Model v2 condition."""
        assessment = self.coordinator.health_v2_assessments.get(self._device_id)
        if assessment is None or assessment.condition_state not in V2_CONDITION_STATES:
            return None
        return assessment.condition_state

    @property
    def icon(self) -> str:
        """Return an icon matching the current v2 production condition."""
        value = self.native_value
        return _HEALTH_ICONS.get(value, _HEALTH_ICONS["unknown"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose only user-facing variable evidence that explains the state."""
        assessment = self.coordinator.health_v2_assessments.get(self._device_id)
        return _public_health_attributes(assessment)


class BatteryHealthSummarySensor(
    CoordinatorEntity[BatteryHealthCoordinator], SensorEntity
):
    """Publish the aggregate actionable Health Model v2 condition."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(V2_CONDITION_STATES)
    _attr_has_entity_name = True
    _attr_name = "Summary"
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self, entry: ConfigEntry, coordinator: BatteryHealthCoordinator
    ) -> None:
        """Initialize the summary entity."""
        super().__init__(coordinator)
        self.entity_id = "sensor.battery_health_summary"
        self._attr_unique_id = f"{entry.entry_id}_health_summary"
        self._attr_device_info = _service_device_info(entry)

    def _state_by_device(self) -> list[tuple[str | None, str]]:
        """Return v2 production conditions paired with readable device names."""
        result: list[tuple[str | None, str]] = []
        for device in self.coordinator.data.devices:
            assessment = self.coordinator.health_v2_assessments.get(device.device_id)
            state = (
                assessment.condition_state
                if assessment is not None
                and assessment.calculation_state != "unavailable"
                and assessment.condition_state in V2_CONDITION_STATES
                else None
            )
            result.append((state, device.device_name or device.device_id[:8]))
        return result

    @property
    def available(self) -> bool:
        """Keep Summary available while at least one device has a v2 condition."""
        state, _counts, _unavailable = summarize_condition_states(
            current for current, _name in self._state_by_device()
        )
        return super().available and state is not None

    @property
    def native_value(self) -> str | None:
        """Return replace > weakening > declining > ok across available devices."""
        state, _counts, _unavailable = summarize_condition_states(
            current for current, _name in self._state_by_device()
        )
        return state

    @property
    def icon(self) -> str:
        """Return an icon matching the aggregate v2 production condition."""
        return _HEALTH_ICONS.get(self.native_value, _HEALTH_ICONS["unknown"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose v2 condition counts, availability and attention lists."""
        state_by_device = self._state_by_device()
        _state, counts, unavailable = summarize_condition_states(
            current for current, _name in state_by_device
        )
        names_by_state = {
            state: [name for current, name in state_by_device if current == state]
            for state in V2_CONDITION_STATES
        }
        unavailable_devices = [
            name for current, name in state_by_device if current is None
        ]
        return {
            "total": len(state_by_device),
            "available": len(state_by_device) - unavailable,
            "unavailable": unavailable,
            "ok": counts["ok"],
            "declining": counts["declining"],
            "weakening": counts["weakening"],
            "replace": counts["replace"],
            "replace_devices": names_by_state["replace"],
            "weakening_devices": names_by_state["weakening"],
            "declining_devices": names_by_state["declining"],
            "unavailable_devices": unavailable_devices,
        }


class BatteryHealthDiscoverySensor(
    CoordinatorEntity[BatteryHealthCoordinator], SensorEntity
):
    """Expose discovery, telemetry and guarded persistence diagnostics."""

    _attr_has_entity_name = True
    _attr_name = "Discovered devices"
    _attr_icon = "mdi:battery-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self, entry: ConfigEntry, coordinator: BatteryHealthCoordinator
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_discovered_devices"
        self._attr_device_info = _service_device_info(entry)

    @property
    def native_value(self) -> int:
        """Return the number of discovered MQTT battery devices."""
        return len(self.coordinator.data.devices)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return compact telemetry, persistence and health diagnostics."""
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
        baseline_v2_counts = {
            "eligible": 0,
            "learning": 0,
            "blocked": 0,
            "not_required": 0,
        }
        cycle_segment_counts = {
            "left_censored": 0,
            "segmented": 0,
            "current_boundary": 0,
            "possible_boundary": 0,
            "insufficient": 0,
        }
        persistence_counts: dict[str, int] = {}
        telemetry_integrity_counts = {
            "trusted": 0,
            "guarded": 0,
            "service_required": 0,
        }
        integrity_incident_active = 0
        integrity_incident_transitions: dict[str, int] = {}
        health_v2_conditions = {state: 0 for state in V2_CONDITION_STATES}
        health_v2_calculation = {state: 0 for state in V2_CALCULATION_STATES}
        health_v2_trends = {state: 0 for state in V2_TREND_STATES}
        health_v2_modes: dict[str, int] = {}
        health_v2_no_condition = 0

        for device in devices:
            diagnostics = device.as_dict()
            diagnostics["battery_percent_now"] = snapshot.battery_percent.get(
                device.device_id
            )
            diagnostics["battery_percent_source"] = snapshot.battery_percent_source.get(
                device.device_id
            )

            battery_history = (
                snapshot.battery_history.get(device.battery_entity_id)
                if device.battery_entity_id is not None
                else None
            )
            if device.battery_entity_id is not None:
                diagnostics["battery_history"] = (
                    battery_history.as_dict() if battery_history is not None else None
                )

            voltage_history = (
                snapshot.voltage_history.get(device.voltage_entity_id)
                if device.voltage_entity_id is not None
                else None
            )
            if device.voltage_entity_id is not None:
                diagnostics["voltage_history"] = (
                    voltage_history.as_dict() if voltage_history is not None else None
                )

            freshness = (
                operability.freshness.get(device.last_seen_entity_id)
                if device.last_seen_entity_id is not None
                else None
            )
            freshness_diagnostics = (
                freshness.as_dict()
                if freshness is not None
                else {
                    "supported": False,
                    "state": "unavailable",
                    "issue": None,
                }
            )
            freshness_diagnostics["reports_24h_semantics"] = (
                "time_balanced_cadence_points"
            )
            freshness_diagnostics["sampling_interval_minutes"] = (
                CADENCE_SAMPLE_INTERVAL_MINUTES
            )
            diagnostics["freshness"] = freshness_diagnostics

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

            integrity = self.coordinator.telemetry_integrity.get(device.device_id)
            diagnostics["telemetry_integrity"] = (
                integrity.as_dict() if integrity is not None else None
            )
            if (
                integrity is not None
                and integrity.state in telemetry_integrity_counts
            ):
                telemetry_integrity_counts[integrity.state] += 1

            incident = self.coordinator.integrity_incident_records.get(device.device_id)
            diagnostics["integrity_incident"] = (
                incident.as_dict() if incident is not None else None
            )
            incident_transition = self.coordinator.integrity_incident_states.get(
                device.device_id
            )
            diagnostics["integrity_incident_transition"] = incident_transition
            if incident is not None:
                integrity_incident_active += 1
            if incident_transition is not None:
                integrity_incident_transitions[incident_transition] = (
                    integrity_incident_transitions.get(incident_transition, 0) + 1
                )

            evidence_model = self.coordinator.evidence_models.get(device.device_id)
            diagnostics["evidence_model"] = (
                evidence_model.as_dict() if evidence_model is not None else None
            )
            if (
                evidence_model is not None
                and evidence_model.decision_readiness in evidence_readiness
            ):
                evidence_readiness[evidence_model.decision_readiness] += 1

            cycle_integrity = self.coordinator.cycle_integrity.get(device.device_id)
            diagnostics["cycle_integrity"] = (
                cycle_integrity.as_dict() if cycle_integrity is not None else None
            )
            if (
                cycle_integrity is not None
                and cycle_integrity.state in cycle_integrity_counts
            ):
                cycle_integrity_counts[cycle_integrity.state] += 1

            baseline_v2 = self.coordinator.baseline_v2_assessments.get(device.device_id)
            persistence = self.coordinator.baseline_v2_persistence.get(device.device_id)
            if baseline_v2 is not None:
                baseline_v2_diagnostics = baseline_v2.as_dict()
                baseline_v2_diagnostics["persistence"] = (
                    persistence.as_dict()
                    if persistence is not None
                    else {
                        "state": "not_evaluated",
                        "changed": False,
                        "persisted": device.device_id
                        in self.coordinator.baseline_v2_records,
                        "record": (
                            self.coordinator.baseline_v2_records[
                                device.device_id
                            ].as_dict()
                            if device.device_id in self.coordinator.baseline_v2_records
                            else None
                        ),
                    }
                )
                diagnostics["baseline_v2"] = baseline_v2_diagnostics
                if baseline_v2.eligibility in baseline_v2_counts:
                    baseline_v2_counts[baseline_v2.eligibility] += 1
                if baseline_v2.cycle_segment.state in cycle_segment_counts:
                    cycle_segment_counts[baseline_v2.cycle_segment.state] += 1
            else:
                diagnostics["baseline_v2"] = None

            if persistence is not None:
                persistence_counts[persistence.state] = (
                    persistence_counts.get(persistence.state, 0) + 1
                )

            health_v2 = self.coordinator.health_v2_assessments.get(device.device_id)
            diagnostics["health"] = (
                health_v2.as_dict() if health_v2 is not None else None
            )
            if health_v2 is not None:
                if health_v2.condition_state in health_v2_conditions:
                    health_v2_conditions[health_v2.condition_state] += 1
                else:
                    health_v2_no_condition += 1
                calc = health_v2.calculation_state
                if calc in health_v2_calculation:
                    health_v2_calculation[calc] += 1
                trend = health_v2.trend_state
                if trend in health_v2_trends:
                    health_v2_trends[trend] += 1
                health_v2_modes[health_v2.assessment_mode] = (
                    health_v2_modes.get(health_v2.assessment_mode, 0) + 1
                )

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
                evidence.state == state for evidence in operability.freshness.values()
            )
            for state in freshness_states
        }

        if health_v2_conditions["replace"]:
            health_v2_summary = "replace"
        elif health_v2_conditions["weakening"]:
            health_v2_summary = "weakening"
        elif health_v2_conditions["declining"]:
            health_v2_summary = "declining"
        elif health_v2_conditions["ok"]:
            health_v2_summary = "ok"
        else:
            health_v2_summary = None

        health_v2_thresholds = {
            "minimum_coverage": V2_MIN_COVERAGE,
            "voltage_declining_ratio": V2_VOLTAGE_DECLINING_RATIO,
            "voltage_weakening_ratio": V2_VOLTAGE_WEAKENING_RATIO,
            "voltage_replace_ratio": V2_VOLTAGE_REPLACE_RATIO,
            "battery_declining_drop_pp": BATTERY_DECLINING_DROP_PP,
            "battery_strong_decline_drop_pp": BATTERY_STRONG_DECLINE_DROP_PP,
            "battery_weakening_drop_pp": BATTERY_WEAKENING_DROP_PP,
            "battery_low_percent_support": BATTERY_LOW_PERCENT,
            "battery_replace_percent_support": BATTERY_REPLACE_PERCENT,
            "battery_replace_7d_percent_support": BATTERY_REPLACE_7D_PERCENT,
            "instantaneous_values_are_primary": False,
            "condition_has_unknown_state": False,
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
            "cadence_sampling_minutes": CADENCE_SAMPLE_INTERVAL_MINUTES,
            "freshness_states": freshness_state_counts,
            "power_outage_supported": len(operability.outages),
            "power_outage_events_24h_total": sum(
                evidence.events_24h or 0 for evidence in operability.outages.values()
            ),
            "telemetry_integrity": telemetry_integrity_counts,
            "integrity_incidents_active": integrity_incident_active,
            "integrity_incident_transitions": integrity_incident_transitions,
            "evidence_readiness": evidence_readiness,
            "cycle_integrity": cycle_integrity_counts,
            "baseline_v2_eligibility": baseline_v2_counts,
            "cycle_segments": cycle_segment_counts,
            "baseline_v2_persisted_records": len(self.coordinator.baseline_v2_records),
            "baseline_v2_persistence": persistence_counts,
            "health_summary": health_v2_summary,
            "health_states": {
                **health_v2_conditions,
                "unavailable": health_v2_no_condition,
            },
            "health_calculation": health_v2_calculation,
            "health_trends": health_v2_trends,
            "health_modes": health_v2_modes,
            "health_thresholds": health_v2_thresholds,
            "devices": device_diagnostics,
        }
