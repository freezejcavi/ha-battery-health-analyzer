"""Persistent live-learned last-seen cadence storage."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .operability import parse_last_seen

CADENCE_STORAGE_KEY = "battery_health.cadence"
CADENCE_STORAGE_VERSION = 1
CADENCE_RETENTION = timedelta(days=7)
CADENCE_SAMPLE_INTERVAL_MINUTES = 15
CADENCE_SAMPLE_INTERVAL = timedelta(minutes=CADENCE_SAMPLE_INTERVAL_MINUTES)
# Seven complete days need at most 7 * 24 * 4 = 672 time-balanced points.
# Keep modest headroom without allowing chatty devices to collapse the horizon.
CADENCE_MAX_SAMPLES = 768
CADENCE_SAVE_DELAY_SECONDS = 300
FUTURE_TOLERANCE = timedelta(minutes=5)


def _bucket_index(timestamp: datetime) -> int:
    """Return the fixed UTC cadence bucket for one aware timestamp."""
    return int(timestamp.timestamp()) // int(CADENCE_SAMPLE_INTERVAL.total_seconds())


class CadenceStore:
    """Keep compact rolling last-seen timestamps learned from live state changes.

    The Store retains at most one representative timestamp per 15-minute bucket.
    This prevents high-rate devices from filling the bounded Store with only a
    few recent minutes while preserving real longer silence gaps for freshness.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the Home Assistant Store wrapper."""
        self._store = Store[dict[str, Any]](
            hass,
            CADENCE_STORAGE_VERSION,
            CADENCE_STORAGE_KEY,
        )
        self._timestamps: dict[str, list[datetime]] = {}

    async def async_load(self) -> None:
        """Load and sanitize persisted timestamp samples."""
        stored = await self._store.async_load() or {}
        devices = stored.get("devices", {})
        if not isinstance(devices, dict):
            return

        observed_at = dt_util.utcnow()
        for device_id, raw in devices.items():
            if not isinstance(device_id, str) or not isinstance(raw, dict):
                continue
            values = raw.get("timestamps", [])
            if not isinstance(values, list):
                continue
            parsed = [
                timestamp
                for value in values
                if (timestamp := parse_last_seen(value)) is not None
            ]
            sanitized = self._sanitize(parsed, observed_at)
            if sanitized:
                self._timestamps[device_id] = sanitized

    @staticmethod
    def _sanitize(
        timestamps: list[datetime],
        observed_at: datetime,
    ) -> list[datetime]:
        """Return recent time-balanced representative cadence points."""
        cutoff = observed_at - CADENCE_RETENTION
        future_limit = observed_at + FUTURE_TOLERANCE
        latest_by_bucket: dict[int, datetime] = {}
        for timestamp in timestamps:
            if not cutoff <= timestamp <= future_limit:
                continue
            bucket = _bucket_index(timestamp)
            previous = latest_by_bucket.get(bucket)
            if previous is None or timestamp > previous:
                latest_by_bucket[bucket] = timestamp

        samples = sorted(latest_by_bucket.values())
        return samples[-CADENCE_MAX_SAMPLES:]

    @callback
    def observe(
        self,
        device_id: str,
        timestamp: datetime,
        observed_at: datetime | None = None,
    ) -> bool:
        """Add one valid live last-seen timestamp and schedule a delayed save."""
        now = observed_at or dt_util.utcnow()
        normalized = parse_last_seen(timestamp)
        if normalized is None:
            return False

        previous = self._timestamps.get(device_id, [])
        # Fast path for chatty devices: update only the current bucket's latest
        # representative instead of repeatedly sorting the full rolling window.
        if previous and _bucket_index(previous[-1]) == _bucket_index(normalized):
            if normalized <= previous[-1]:
                return False
            samples = [*previous[:-1], normalized]
        else:
            samples = self._sanitize([*previous, normalized], now)

        if samples == previous:
            return False

        self._timestamps[device_id] = samples
        self._store.async_delay_save(self._data_to_save, CADENCE_SAVE_DELAY_SECONDS)
        return True

    @callback
    def timestamps(
        self,
        device_id: str,
        observed_at: datetime | None = None,
    ) -> tuple[datetime, ...]:
        """Return recent cadence samples for one stable HA device id."""
        now = observed_at or dt_util.utcnow()
        samples = self._sanitize(self._timestamps.get(device_id, []), now)
        if samples:
            self._timestamps[device_id] = samples
        else:
            self._timestamps.pop(device_id, None)
        return tuple(samples)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        """Serialize compact cadence samples."""
        return {
            "devices": {
                device_id: {
                    "timestamps": [timestamp.isoformat() for timestamp in timestamps]
                }
                for device_id, timestamps in self._timestamps.items()
                if timestamps
            }
        }

    async def async_save(self) -> None:
        """Persist immediately, used during clean integration unload."""
        await self._store.async_save(self._data_to_save())
