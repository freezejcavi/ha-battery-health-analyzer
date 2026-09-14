"""Data models for Battery Health Analyzer discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class EntityDescriptor:
    """Small Home Assistant entity snapshot used by the discovery engine."""

    entity_id: str
    device_id: str | None
    domain: str
    state: str | None = None
    device_class: str | None = None
    unit: str | None = None
    name: str | None = None
    original_name: str | None = None
    disabled: bool = False

    @property
    def searchable_name(self) -> str:
        """Return normalized text used for conservative fallback matching."""
        values = (self.entity_id, self.name or "", self.original_name or "")
        return " ".join(values).lower().replace("-", "_").replace(" ", "_")


@dataclass(frozen=True, slots=True)
class DiscoveredBatteryDevice:
    """Source entities selected for one physical Home Assistant device."""

    device_id: str
    device_name: str | None
    battery_entity_id: str | None
    battery_low_entity_id: str | None
    voltage_entity_id: str | None
    last_seen_entity_id: str | None
    outage_entity_id: str | None
    issues: tuple[str, ...] = ()
    ambiguous_candidates: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def has_voltage(self) -> bool:
        """Return whether a usable voltage source was selected."""
        return self.voltage_entity_id is not None

    @property
    def is_ambiguous(self) -> bool:
        """Return whether at least one source role could not be selected safely."""
        return any(issue.startswith("ambiguous_") for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        """Return a stable diagnostics representation."""
        result = {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "battery_entity_id": self.battery_entity_id,
            "battery_low_entity_id": self.battery_low_entity_id,
            "voltage_entity_id": self.voltage_entity_id,
            "last_seen_entity_id": self.last_seen_entity_id,
            "outage_entity_id": self.outage_entity_id,
            "issues": list(self.issues),
        }
        if self.ambiguous_candidates:
            result["ambiguous_candidates"] = {
                role: list(entity_ids)
                for role, entity_ids in self.ambiguous_candidates
            }
        return result


@dataclass(frozen=True, slots=True)
class VoltageHistoryPoint:
    """One voltage state transition used by the pure statistics engine."""

    timestamp: datetime
    voltage_mv: float | None


@dataclass(frozen=True, slots=True)
class VoltageHistorySummary:
    """Compact result of one entity's Recorder window analysis."""

    median_mv: float | None
    coverage_ratio: float
    valid_duration_seconds: float
    source_points: int
    latest_voltage_mv: float | None
    source_units: tuple[str, ...] = ()
    issue: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return rounded diagnostics suitable for an HA state attribute."""
        return {
            "median_24h_mv": (
                round(self.median_mv) if self.median_mv is not None else None
            ),
            "coverage_ratio": round(self.coverage_ratio, 3),
            "valid_duration_hours": round(
                self.valid_duration_seconds / 3600, 2
            ),
            "source_points": self.source_points,
            "latest_voltage_mv": (
                round(self.latest_voltage_mv)
                if self.latest_voltage_mv is not None
                else None
            ),
            "source_units": list(self.source_units),
            "issue": self.issue,
        }


@dataclass(frozen=True, slots=True)
class BatteryHealthSnapshot:
    """One coordinated discovery and Recorder analysis snapshot."""

    devices: tuple[DiscoveredBatteryDevice, ...]
    voltage_history: dict[str, VoltageHistorySummary]
