"""Guarded persistent baseline v2 state for Battery Health Analyzer."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .baseline_v2 import BaselineV2Assessment

_LOGGER = logging.getLogger(__name__)

BASELINE_V2_STORAGE_KEY = "battery_health.baselines_v2"
BASELINE_V2_STORAGE_VERSION = 1
BASELINE_V2_RAISE_MIN_RATIO = 1.01


@dataclass(frozen=True, slots=True)
class BaselineV2Record:
    """Compact persisted baseline state for one guarded battery cycle."""

    baseline_mv: float
    confidence: float
    cycle_generation: int
    boundary_date: str | None
    cycle_start_known: bool
    anchor: str
    source: str
    created_at: datetime
    updated_at: datetime

    def as_storage_dict(self) -> dict[str, Any]:
        """Serialize one record for Home Assistant Store."""
        return {
            "baseline_mv": self.baseline_mv,
            "confidence": self.confidence,
            "cycle_generation": self.cycle_generation,
            "boundary_date": self.boundary_date,
            "cycle_start_known": self.cycle_start_known,
            "anchor": self.anchor,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> BaselineV2Record:
        """Deserialize and validate one stored record."""
        baseline_mv = float(data["baseline_mv"])
        confidence = float(data["confidence"])
        cycle_generation = int(data["cycle_generation"])
        if not isfinite(baseline_mv) or baseline_mv <= 0:
            raise ValueError("baseline_mv must be a positive finite value")
        if not isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be within 0..1")
        if cycle_generation < 1:
            raise ValueError("cycle_generation must be >= 1")
        boundary_date = data.get("boundary_date")
        if boundary_date is not None and not isinstance(boundary_date, str):
            raise TypeError("boundary_date must be a string or null")
        return cls(
            baseline_mv=baseline_mv,
            confidence=confidence,
            cycle_generation=cycle_generation,
            boundary_date=boundary_date,
            cycle_start_known=bool(data.get("cycle_start_known", False)),
            anchor=str(data["anchor"]),
            source=str(data["source"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics."""
        return {
            "baseline_mv": round(self.baseline_mv),
            "confidence": round(self.confidence, 3),
            "cycle_generation": self.cycle_generation,
            "boundary_date": self.boundary_date,
            "cycle_start_known": self.cycle_start_known,
            "anchor": self.anchor,
            "source": self.source,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BaselineV2PersistenceResult:
    """One guarded persistence decision."""

    record: BaselineV2Record | None
    state: str
    changed: bool

    def as_dict(self) -> dict[str, Any]:
        """Return diagnostics without exposing Store implementation details."""
        return {
            "state": self.state,
            "changed": self.changed,
            "persisted": self.record is not None,
            "record": self.record.as_dict() if self.record is not None else None,
        }


def _new_record(
    assessment: BaselineV2Assessment,
    observed_at: datetime,
    *,
    cycle_generation: int,
    boundary_date: str | None,
) -> BaselineV2Record:
    candidate = assessment.candidate_mv
    source = assessment.candidate_source
    if candidate is None or source is None:
        raise ValueError("eligible assessment must provide candidate and source")
    return BaselineV2Record(
        baseline_mv=float(candidate),
        confidence=float(assessment.confidence),
        cycle_generation=cycle_generation,
        boundary_date=boundary_date,
        cycle_start_known=assessment.cycle_segment.cycle_start_known,
        anchor=assessment.anchor,
        source=source,
        created_at=observed_at,
        updated_at=observed_at,
    )


def plan_baseline_v2_persistence(
    existing: BaselineV2Record | None,
    assessment: BaselineV2Assessment,
    observed_at: datetime,
) -> BaselineV2PersistenceResult:
    """Plan one conservative Store transition without performing I/O.

    Only an `eligible` guarded assessment may create or alter persistent state.
    Learning, blocked and not-required states never delete or rewrite an existing
    record. Within one cycle a baseline may only rise materially; it never learns
    downward. A new cycle generation is created only after an eligible segmented
    assessment exposes a different confirmed boundary date.
    """
    if (
        assessment.eligibility != "eligible"
        or assessment.candidate_mv is None
        or assessment.candidate_source is None
    ):
        return BaselineV2PersistenceResult(
            existing,
            "retained_guarded" if existing is not None else "not_persisted",
            False,
        )

    segment = assessment.cycle_segment
    assessment_boundary = (
        segment.boundary_date if segment.state == "segmented" else None
    )

    if existing is None:
        record = _new_record(
            assessment,
            observed_at,
            cycle_generation=1,
            boundary_date=assessment_boundary,
        )
        return BaselineV2PersistenceResult(record, "created", True)

    if (
        assessment_boundary is not None
        and assessment_boundary != existing.boundary_date
    ):
        record = _new_record(
            assessment,
            observed_at,
            cycle_generation=existing.cycle_generation + 1,
            boundary_date=assessment_boundary,
        )
        return BaselineV2PersistenceResult(record, "cycle_started", True)

    baseline_mv = existing.baseline_mv
    source = existing.source
    state = "retained"
    if assessment.candidate_mv >= existing.baseline_mv * BASELINE_V2_RAISE_MIN_RATIO:
        baseline_mv = float(assessment.candidate_mv)
        source = assessment.candidate_source
        state = "raised"

    confidence = max(existing.confidence, assessment.confidence)
    cycle_start_known = (
        existing.cycle_start_known or assessment.cycle_segment.cycle_start_known
    )
    anchor = existing.anchor
    if assessment.anchor == "observed_cycle_boundary":
        anchor = assessment.anchor

    metadata_changed = (
        confidence != existing.confidence
        or cycle_start_known != existing.cycle_start_known
        or anchor != existing.anchor
    )
    changed = state == "raised" or metadata_changed
    if not changed:
        return BaselineV2PersistenceResult(existing, "retained", False)

    record = BaselineV2Record(
        baseline_mv=baseline_mv,
        confidence=confidence,
        cycle_generation=existing.cycle_generation,
        boundary_date=existing.boundary_date,
        cycle_start_known=cycle_start_known,
        anchor=anchor,
        source=source,
        created_at=existing.created_at,
        updated_at=observed_at,
    )
    if state != "raised":
        state = "metadata_updated"
    return BaselineV2PersistenceResult(record, state, True)


class BaselineV2Store:
    """Load and persist guarded baseline v2 records in a separate Store."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](
            hass,
            BASELINE_V2_STORAGE_VERSION,
            BASELINE_V2_STORAGE_KEY,
        )
        self.records: dict[str, BaselineV2Record] = {}

    async def async_load(self) -> None:
        """Load valid v2 records without importing legacy baseline state."""
        stored = await self._store.async_load() or {}
        records = stored.get("devices", {})
        if not isinstance(records, dict):
            return
        for device_id, data in records.items():
            if not isinstance(device_id, str) or not isinstance(data, dict):
                continue
            try:
                self.records[device_id] = BaselineV2Record.from_storage_dict(data)
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid guarded baseline v2 for %s", device_id)

    def apply(
        self,
        device_id: str,
        assessment: BaselineV2Assessment,
        observed_at: datetime,
    ) -> BaselineV2PersistenceResult:
        """Apply one pure persistence plan to the in-memory Store snapshot."""
        result = plan_baseline_v2_persistence(
            self.records.get(device_id),
            assessment,
            observed_at,
        )
        if result.changed and result.record is not None:
            self.records[device_id] = result.record
        return result

    async def async_save(self) -> None:
        """Persist the complete compact v2 snapshot."""
        await self._store.async_save(
            {
                "devices": {
                    device_id: record.as_storage_dict()
                    for device_id, record in self.records.items()
                }
            }
        )
