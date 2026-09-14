"""Persistent baseline storage for Battery Health Analyzer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import BaselineRecord

_LOGGER = logging.getLogger(__name__)


class BaselineStore:
    """Load and save compact per-device baseline records."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize Home Assistant Store."""
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            STORAGE_KEY,
        )
        self.records: dict[str, BaselineRecord] = {}

    async def async_load(self) -> None:
        """Load valid records and ignore individual malformed entries."""
        stored = await self._store.async_load() or {}
        records = stored.get("devices", {})
        if not isinstance(records, dict):
            return

        for device_id, data in records.items():
            if not isinstance(device_id, str) or not isinstance(data, dict):
                continue
            try:
                self.records[device_id] = BaselineRecord.from_storage_dict(data)
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid stored baseline for %s", device_id)

    async def async_save(self) -> None:
        """Persist the current compact baseline snapshot."""
        await self._store.async_save(
            {
                "devices": {
                    device_id: record.as_storage_dict()
                    for device_id, record in self.records.items()
                }
            }
        )
