"""Data models for Battery Health Analyzer discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
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
class BatteryHistoryPoint:
    """One battery-percentage state transition used by the statistics engine."""

    timestamp: datetime
    battery_percent: float | None


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
    p10_mv: float | None = None
    p90_mv: float | None = None
    min_mv: float | None = None
    max_mv: float | None = None
    range_mv: float | None = None
    value_changes: int = 0

    @property
    def history_rows(self) -> int:
        """Return Recorder history rows after timestamp de-duplication."""
        return self.source_points

    def as_dict(self) -> dict[str, Any]:
        """Return rounded diagnostics suitable for an HA state attribute."""
        return {
            "p10_24h_mv": round(self.p10_mv) if self.p10_mv is not None else None,
            "p50_24h_mv": (
                round(self.median_mv) if self.median_mv is not None else None
            ),
            "p90_24h_mv": round(self.p90_mv) if self.p90_mv is not None else None,
            "min_24h_mv": round(self.min_mv) if self.min_mv is not None else None,
            "max_24h_mv": round(self.max_mv) if self.max_mv is not None else None,
            "range_24h_mv": (
                round(self.range_mv) if self.range_mv is not None else None
            ),
            "coverage_ratio": round(self.coverage_ratio, 3),
            "valid_duration_hours": round(
                self.valid_duration_seconds / 3600, 2
            ),
            "history_rows": self.history_rows,
            "value_changes": self.value_changes,
            "latest_voltage_mv": (
                round(self.latest_voltage_mv)
                if self.latest_voltage_mv is not None
                else None
            ),
            "source_units": list(self.source_units),
            "issue": self.issue,
        }


@dataclass(frozen=True, slots=True)
class BatteryHistorySummary:
    """Compact result of one battery percentage Recorder window."""

    p10_percent: float | None
    median_percent: float | None
    p90_percent: float | None
    min_percent: float | None
    max_percent: float | None
    range_percent: float | None
    coverage_ratio: float
    valid_duration_seconds: float
    history_rows: int
    value_changes: int
    latest_percent: float | None
    issue: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return rounded battery diagnostics for an HA state attribute."""
        def rounded(value: float | None) -> float | None:
            return round(value, 1) if value is not None else None

        return {
            "p10_24h_percent": rounded(self.p10_percent),
            "p50_24h_percent": rounded(self.median_percent),
            "p90_24h_percent": rounded(self.p90_percent),
            "min_24h_percent": rounded(self.min_percent),
            "max_24h_percent": rounded(self.max_percent),
            "range_24h_percent": rounded(self.range_percent),
            "coverage_ratio": round(self.coverage_ratio, 3),
            "valid_duration_hours": round(
                self.valid_duration_seconds / 3600, 2
            ),
            "history_rows": self.history_rows,
            "value_changes": self.value_changes,
            "latest_percent": rounded(self.latest_percent),
            "issue": self.issue,
        }


@dataclass(frozen=True, slots=True)
class RecorderHistorySnapshot:
    """Combined result of one voltage and battery Recorder query."""

    voltage_history: dict[str, VoltageHistorySummary]
    battery_percent: dict[str, float | None]
    battery_percent_source: dict[str, str]
    battery_history: dict[str, BatteryHistorySummary] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BaselineRecord:
    """Persistent healthy-voltage baseline for one HA device."""

    baseline_mv: float
    first_qualified_at: datetime
    last_qualified_at: datetime
    sample_count: int
    battery_cycle: int = 1

    def as_storage_dict(self) -> dict[str, Any]:
        """Serialize the record for Home Assistant Store."""
        return {
            "baseline_mv": self.baseline_mv,
            "first_qualified_at": self.first_qualified_at.isoformat(),
            "last_qualified_at": self.last_qualified_at.isoformat(),
            "sample_count": self.sample_count,
            "battery_cycle": self.battery_cycle,
        }

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> BaselineRecord:
        """Deserialize and validate one stored baseline record."""
        return cls(
            baseline_mv=float(data["baseline_mv"]),
            first_qualified_at=datetime.fromisoformat(data["first_qualified_at"]),
            last_qualified_at=datetime.fromisoformat(data["last_qualified_at"]),
            sample_count=int(data["sample_count"]),
            battery_cycle=int(data.get("battery_cycle", 1)),
        )


@dataclass(frozen=True, slots=True)
class BaselineLearningResult:
    """Diagnostic outcome of one baseline-learning evaluation."""

    record: BaselineRecord | None
    state: str
    confidence: float
    changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics for the HA entity."""
        return {
            "baseline_mv": (
                round(self.record.baseline_mv) if self.record is not None else None
            ),
            "confidence": round(self.confidence, 3),
            "sample_count": (
                self.record.sample_count if self.record is not None else 0
            ),
            "battery_cycle": (
                self.record.battery_cycle if self.record is not None else None
            ),
            "learning_state": self.state,
        }


@dataclass(frozen=True, slots=True)
class BatteryHealthSnapshot:
    """One coordinated discovery and Recorder analysis snapshot."""

    devices: tuple[DiscoveredBatteryDevice, ...]
    voltage_history: dict[str, VoltageHistorySummary]
    battery_percent: dict[str, float | None]
    battery_percent_source: dict[str, str]
    baseline_learning: dict[str, BaselineLearningResult]
    battery_history: dict[str, BatteryHistorySummary] = field(default_factory=dict)
