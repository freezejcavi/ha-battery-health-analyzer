"""Update coordinator for Battery Health Analyzer."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .baseline import learn_baseline
from .const import ANALYSIS_INTERVAL, DOMAIN
from .ha_discovery import async_discover_battery_devices
from .models import BaselineLearningResult, BatteryHealthSnapshot
from .recorder import async_get_recorder_history
from .storage import BaselineStore

_LOGGER = logging.getLogger(__name__)


class BatteryHealthCoordinator(DataUpdateCoordinator[BatteryHealthSnapshot]):
    """Coordinate read-only discovery and batch Recorder analysis."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=ANALYSIS_INTERVAL,
        )
        self._baseline_store = BaselineStore(hass)

    async def async_initialize(self) -> None:
        """Load persistent state before the first coordinated refresh."""
        await self._baseline_store.async_load()

    async def _async_update_data(self) -> BatteryHealthSnapshot:
        """Return one discovery and Recorder snapshot without source writes."""
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
        observed_at = dt_util.utcnow()
        recorder_history = await async_get_recorder_history(
            self.hass,
            voltage_entity_ids,
            battery_entity_ids,
            observed_at,
        )
        voltage_history = recorder_history.voltage_history
        battery_percent: dict[str, float | None] = {}
        battery_percent_source: dict[str, str] = {}
        baseline_learning: dict[str, BaselineLearningResult] = {}
        baseline_changed = False

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
            learning_result = learn_baseline(
                self._baseline_store.records.get(device.device_id),
                history_summary,
                percentage,
                observed_at,
            )
            baseline_learning[device.device_id] = learning_result
            if learning_result.changed and learning_result.record is not None:
                self._baseline_store.records[device.device_id] = (
                    learning_result.record
                )
                baseline_changed = True

        if baseline_changed:
            await self._baseline_store.async_save()

        return BatteryHealthSnapshot(
            devices=devices,
            voltage_history=voltage_history,
            battery_percent=battery_percent,
            battery_percent_source=battery_percent_source,
            baseline_learning=baseline_learning,
        )
