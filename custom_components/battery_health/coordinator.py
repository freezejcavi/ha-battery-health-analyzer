"""Update coordinator for Battery Health Analyzer."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import ANALYSIS_INTERVAL, DOMAIN
from .ha_discovery import async_discover_battery_devices
from .models import BatteryHealthSnapshot
from .recorder import async_get_voltage_history

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

    async def _async_update_data(self) -> BatteryHealthSnapshot:
        """Return one discovery and Recorder snapshot without source writes."""
        devices = tuple(async_discover_battery_devices(self.hass))
        voltage_entity_ids = sorted(
            device.voltage_entity_id
            for device in devices
            if device.voltage_entity_id is not None
        )
        voltage_history = await async_get_voltage_history(
            self.hass,
            voltage_entity_ids,
            dt_util.utcnow(),
        )
        return BatteryHealthSnapshot(
            devices=devices,
            voltage_history=voltage_history,
        )
