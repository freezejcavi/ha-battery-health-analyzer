"""Update coordinator for Battery Health Analyzer."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .baseline import learn_baseline
from .const import ANALYSIS_INTERVAL, DOMAIN
from .ha_discovery import async_discover_battery_devices
from .models import (
    BaselineLearningResult,
    BatteryHealthSnapshot,
    LongTermHistorySnapshot,
    TelemetryProfile,
)
from .operability import OperabilitySnapshot
from .operability_recorder import async_get_operability_history
from .profiler import build_telemetry_profile
from .recorder import (
    async_get_long_term_history,
    async_get_recorder_history,
)
from .storage import BaselineStore

_LOGGER = logging.getLogger(__name__)


class BatteryHealthCoordinator(DataUpdateCoordinator[BatteryHealthSnapshot]):
    """Coordinate read-only discovery and Recorder telemetry analysis."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=ANALYSIS_INTERVAL,
        )
        self._baseline_store = BaselineStore(hass)
        self._long_term_history: LongTermHistorySnapshot | None = None
        self.operability = OperabilitySnapshot({}, {})

    async def async_initialize(self) -> None:
        """Load existing persistent state before the first refresh."""
        await self._baseline_store.async_load()

    async def _async_update_data(self) -> BatteryHealthSnapshot:
        """Return one read-only telemetry snapshot without Store writes."""
        devices = tuple(async_discover_battery_devices(self.hass))
        voltage_entity_ids = sorted(
            device.voltage_entity_id
            for device in devices
            if device.voltage_entity_id is not None
        )
        battery_entity_ids = sorted(
            device.battery_entity_id
            for device in devices
            if device.battery_entity_id is not None
        )
        temperature_entity_ids = sorted(
            device.temperature_entity_id
            for device in devices
            if device.temperature_entity_id is not None
        )
        last_seen_entity_ids = sorted(
            device.last_seen_entity_id
            for device in devices
            if device.last_seen_entity_id is not None
        )
        outage_entity_ids = sorted(
            device.outage_entity_id
            for device in devices
            if device.outage_entity_id is not None
        )
        observed_at = dt_util.utcnow()

        recorder_history = await async_get_recorder_history(
            self.hass,
            voltage_entity_ids,
            battery_entity_ids,
            observed_at,
        )
        self.operability = await async_get_operability_history(
            self.hass,
            last_seen_entity_ids,
            outage_entity_ids,
            observed_at,
        )
        if self._long_term_history is None:
            self._long_term_history = await async_get_long_term_history(
                self.hass,
                voltage_entity_ids,
                battery_entity_ids,
                temperature_entity_ids,
                observed_at,
            )

        voltage_history = recorder_history.voltage_history
        battery_percent: dict[str, float | None] = {}
        battery_percent_source: dict[str, str] = {}
        baseline_learning: dict[str, BaselineLearningResult] = {}
        telemetry_profiles: dict[str, TelemetryProfile] = {}

        for device in devices:
            percentage = (
                recorder_history.battery_percent.get(device.battery_entity_id)
                if device.battery_entity_id is not None
                else None
            )
            battery_percent[device.device_id] = percentage
            battery_percent_source[device.device_id] = (
                recorder_history.battery_percent_source.get(
                    device.battery_entity_id,
                    "unavailable",
                )
                if device.battery_entity_id is not None
                else "unavailable"
            )

            history_summary = (
                voltage_history.get(device.voltage_entity_id)
                if device.voltage_entity_id is not None
                else None
            )
            baseline_learning[device.device_id] = learn_baseline(
                self._baseline_store.records.get(device.device_id),
                history_summary,
                percentage,
                observed_at,
            )

            battery_daily = (
                self._long_term_history.battery_daily.get(
                    device.battery_entity_id,
                    {},
                )
                if device.battery_entity_id is not None
                else {}
            )
            voltage_daily = (
                self._long_term_history.voltage_daily.get(
                    device.voltage_entity_id,
                    {},
                )
                if device.voltage_entity_id is not None
                else {}
            )
            temperature_daily = (
                self._long_term_history.temperature_daily.get(
                    device.temperature_entity_id,
                    {},
                )
                if device.temperature_entity_id is not None
                else {}
            )
            telemetry_profiles[device.device_id] = build_telemetry_profile(
                battery_daily,
                voltage_daily,
                temperature_daily,
            )

        return BatteryHealthSnapshot(
            devices=devices,
            voltage_history=voltage_history,
            battery_percent=battery_percent,
            battery_percent_source=battery_percent_source,
            baseline_learning=baseline_learning,
            battery_history=recorder_history.battery_history,
            telemetry_profiles=telemetry_profiles,
        )
