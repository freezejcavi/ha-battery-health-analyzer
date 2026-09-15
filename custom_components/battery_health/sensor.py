"""Sensor platform for Battery Health Analyzer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cadence_store import CADENCE_SAMPLE_INTERVAL_MINUTES
from .const import DOMAIN, NAME, SOURCE_PLATFORM
from .coordinator import BatteryHealthCoordinator
from .health import (
    BATTERY_ONLY_LOW_MAX_PERCENT,
    BATTERY_ONLY_LOW_UPPER_MAX_PERCENT,
    BATTERY_ONLY_OK_MIN_PERCENT,
    HEALTH_MIN_COVERAGE,
    HEALTH_STATES,
    VOLTAGE_OK_RATIO,
    VOLTAGE_REPLACE_RATIO,
    summarize_health_states,
)
from .health_v2 import (
    BATTERY_DECLINING_DROP_PP,
    BATTERY_LOW_PERCENT,
    BATTERY_REPLACE_7D_PERCENT,
    BATTERY_REPLACE_PERCENT,
    BATTERY_STRONG_DECLINE_DROP_PP,
    BATTERY_WEAKENING_DROP_PP,
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
    "weakening": "mdi:battery-alert",
    "replace": "mdi:battery-alert-variant-outline",
    "unknown": "mdi:battery-unknown",
}


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
    _attr_options = list(HEALTH_STATES)
    _attr_has_entity_name = False
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: BatteryHealthCoordinator,
        device_id: str,
        device_name: str | None,
    ) -> None:
        """Initialize one stable per-device health entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        display_name = device_name or device_id[:8]
        self._attr_name = f"{display_name} Battery health"
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
    def native_value(self) -> str:
        """Return the dev23 production state unchanged during dev24 shadowing."""
        assessment = self.coordinator.health_assessments.get(self._device_id)
        if assessment is None or assessment.candidate_state not in HEALTH_STATES:
            return "unknown"
        return assessment.candidate_state

    @property
    def icon(self) -> str:
        """Return an icon matching the current production health state."""
        return _HEALTH_ICONS.get(self.native_value, _HEALTH_ICONS["unknown"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose compact decision evidence without recording attribute churn."""
        assessment = self.coordinator.health_assessments.get(self._device_id)
        device = self._device()
        if assessment is None:
            return {
                "confidence": 0.0,
                "decision_path": "unavailable",
                "reasons": ["health_assessment_unavailable"],
                "limitations": [],
                "source_device_id": self._device_id,
            }

        diagnostics = assessment.as_dict()
        metrics = diagnostics["metrics"]
        record = self.coordinator.baseline_v2_records.get(self._device_id)
        return {
            "confidence": diagnostics["confidence"],
            "decision_path": assessment.decision_path,
            "voltage_health_ratio": metrics["voltage_health_ratio"],
            "battery_level_percent": metrics["battery_level_percent"],
            "baseline_mv": metrics["baseline_mv"],
            "current_voltage_p90_mv": metrics["current_voltage_p90_mv"],
            "reasons": list(assessment.reasons),
            "limitations": list(assessment.limitations),
            "cycle_generation": (
                record.cycle_generation if record is not None else None
            ),
            "source_device_id": self._device_id,
            "battery_entity_id": (
                device.battery_entity_id if device is not None else None
            ),
            "voltage_entity_id": (
                device.voltage_entity_id if device is not None else None
            ),
            "temperature_entity_id": (
                device.temperature_entity_id if device is not None else None
            ),
        }


class BatteryHealthSummarySensor(
    CoordinatorEntity[BatteryHealthCoordinator], SensorEntity
):
    """Publish the aggregate actionable dev23 battery-health state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(HEALTH_STATES)
    _attr_has_entity_name = True
    _attr_name = "Summary"
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self, entry: ConfigEntry, coordinator: BatteryHealthCoordinator
    ) -> None:
        """Initialize the summary entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_health_summary"
        self._attr_device_info = _service_device_info(entry)

    def _state_by_device(self) -> list[tuple[str, str]]:
        """Return current production health state paired with a readable name."""
        result: list[tuple[str, str]] = []
        for device in self.coordinator.data.devices:
            assessment = self.coordinator.health_assessments.get(device.device_id)
            state = (
                assessment.candidate_state
                if assessment is not None
                and assessment.candidate_state in HEALTH_STATES
                else "unknown"
            )
            result.append((state, device.device_name or device.device_id[:8]))
        return result

    @property
    def native_value(self) -> str:
        """Return the highest actionable aggregate production state."""
        state, _counts = summarize_health_states(
            state for state, _name in self._state_by_device()
        )
        return state

    @property
    def icon(self) -> str:
        """Return an icon matching the aggregate production state."""
        return _HEALTH_ICONS.get(self.native_value, _HEALTH_ICONS["unknown"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose compact production-state counts and attention lists."""
        state_by_device = self._state_by_device()
        _state, counts = summarize_health_states(
            state for state, _name in state_by_device
        )
        names_by_state = {
            state: [name for current, name in state_by_device if current == state]
            for state in HEALTH_STATES
        }
        return {
            "total": len(state_by_device),
            "ok": counts["ok"],
            "weakening": counts["weakening"],
            "replace": counts["replace"],
            "unknown": counts["unknown"],
            "replace_devices": names_by_state["replace"],
            "weakening_devices": names_by_state["weakening"],
            "unknown_devices": names_by_state["unknown"],
        }


class BatteryHealthDiscoverySensor(
    CoordinatorEntity[BatteryHealthCoordinator], SensorEntity
):
    """Expose discovery, telemetry and guarded persistence diagnostics."""

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
        health_counts = {state: 0 for state in HEALTH_STATES}
        health_paths: dict[str, int] = {}
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

            health = self.coordinator.health_assessments.get(device.device_id)
            diagnostics["health_shadow"] = (
                health.as_dict() if health is not None else None
            )
            if health is not None:
                state = (
                    health.candidate_state
                    if health.candidate_state in health_counts
                    else "unknown"
                )
                health_counts[state] += 1
                health_paths[health.decision_path] = (
                    health_paths.get(health.decision_path, 0) + 1
                )

            health_v2 = self.coordinator.health_v2_assessments.get(device.device_id)
            diagnostics["health_v2_shadow"] = (
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

        summary_state, _summary_counts = summarize_health_states(
            health.candidate_state
            for health in self.coordinator.health_assessments.values()
        )

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

        thresholds = {
            "voltage_ok_ratio": VOLTAGE_OK_RATIO,
            "voltage_replace_ratio": VOLTAGE_REPLACE_RATIO,
            "minimum_coverage": HEALTH_MIN_COVERAGE,
            "battery_only_ok_min_percent": BATTERY_ONLY_OK_MIN_PERCENT,
            "battery_only_low_max_percent": BATTERY_ONLY_LOW_MAX_PERCENT,
            "battery_only_low_upper_max_percent": (BATTERY_ONLY_LOW_UPPER_MAX_PERCENT),
            "battery_only_replace_allowed": False,
        }
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
            "evidence_readiness": evidence_readiness,
            "cycle_integrity": cycle_integrity_counts,
            "baseline_v2_eligibility": baseline_v2_counts,
            "cycle_segments": cycle_segment_counts,
            "baseline_v2_persisted_records": len(self.coordinator.baseline_v2_records),
            "baseline_v2_persistence": persistence_counts,
            "health_summary": summary_state,
            "health_states": health_counts,
            "health_paths": health_paths,
            "health_thresholds": thresholds,
            # Dev24 shadow model; production entities remain on the dev23 model.
            "health_v2_summary": health_v2_summary,
            "health_v2_conditions": health_v2_conditions,
            "health_v2_without_condition": health_v2_no_condition,
            "health_v2_calculation": health_v2_calculation,
            "health_v2_trends": health_v2_trends,
            "health_v2_modes": health_v2_modes,
            "health_v2_thresholds": health_v2_thresholds,
            # Retained for parity/history during the transition.
            "shadow_health_candidates": health_counts,
            "shadow_health_paths": health_paths,
            "shadow_health_thresholds": thresholds,
            "devices": device_diagnostics,
        }
