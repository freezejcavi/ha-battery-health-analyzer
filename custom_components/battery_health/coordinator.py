"""Update coordinator for Battery Health Analyzer."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DISCOVERY_INTERVAL, DOMAIN
from .ha_discovery import async_discover_battery_devices
from .models import DiscoveredBatteryDevice

_LOGGER = logging.getLogger(__name__)


class BatteryHealthCoordinator(DataUpdateCoordinator[list[DiscoveredBatteryDevice]]):
    """Coordinate the read-only discovery snapshot."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DISCOVERY_INTERVAL,
        )

    async def _async_update_data(self) -> list[DiscoveredBatteryDevice]:
        """Return the current discovery snapshot without changing source devices."""
        return async_discover_battery_devices(self.hass)

