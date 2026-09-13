"""Data models for Battery Health Analyzer discovery."""

from __future__ import annotations

from dataclasses import dataclass
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
