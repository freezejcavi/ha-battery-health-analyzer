"""Update coordinator for Battery Health Analyzer."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .baseline import learn_baseline, parse_battery_percent
from .const import ANALYSIS_INTERVAL, DOMAIN
from .ha_discovery import async_discover_battery_devices
from .models import BaselineLearningResult, BatteryHealthSnapshot
from .recorder import async_get_voltage_history
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
        observed_at = dt_util.utcnow()
        voltage_history = await async_get_voltage_history(
            self.hass,
            voltage_entity_ids,
            observed_at,
        )
        battery_percent: dict[str, float | None] = {}
        baseline_learning: dict[str, BaselineLearningResult] = {}
        baseline_changed = False

        for device in devices:
            battery_state = (
                self.hass.states.get(device.battery_entity_id)
                if device.battery_entity_id is not None
                else None
            )
            percentage = parse_battery_percent(
                battery_state.state if battery_state is not None else None
            )
            battery_percent[device.device_id] = percentage
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
            baseline_learning=baseline_learning,
        )
