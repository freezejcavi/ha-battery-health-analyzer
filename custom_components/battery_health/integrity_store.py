"""Persistent latch for severe telemetry-integrity incidents."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .operability import parse_last_seen
from .telemetry_integrity import TelemetryIntegrityAssessment

_LOGGER = logging.getLogger(__name__)

INTEGRITY_INCIDENT_STORAGE_KEY = "battery_health.integrity_incidents"
INTEGRITY_INCIDENT_STORAGE_VERSION = 1
INTEGRITY_RECOVERY_REPORTS_REQUIRED = 3

# A historical outage jump remains useful for bootstrapping/latching an incident,
# but once all current telemetry is clean it must not prevent recovery forever.
_RECOVERY_TOLERATED_FINDINGS = frozenset(
    {
        "outage_counter_implausible_jump",
        "outage_counter_jump_retained_7d",
    }
)


@dataclass(frozen=True, slots=True)
class IntegrityIncidentRecord:
    """One active severe telemetry-integrity incident."""

    first_seen: datetime
    last_bad_at: datetime
    last_bad_report_at: datetime | None
    findings: tuple[str, ...]
    clean_reports: int = 0
    last_recovery_report_at: datetime | None = None

    def as_storage_dict(self) -> dict[str, Any]:
        """Serialize one active incident."""
        return {
            "first_seen": self.first_seen.isoformat(),
            "last_bad_at": self.last_bad_at.isoformat(),
            "last_bad_report_at": (
                self.last_bad_report_at.isoformat()
                if self.last_bad_report_at is not None
                else None
            ),
            "findings": list(self.findings),
            "clean_reports": self.clean_reports,
            "last_recovery_report_at": (
                self.last_recovery_report_at.isoformat()
                if self.last_recovery_report_at is not None
                else None
            ),
        }

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> IntegrityIncidentRecord:
        """Deserialize and validate one incident record."""
        first_seen = parse_last_seen(data.get("first_seen"))
        last_bad_at = parse_last_seen(data.get("last_bad_at"))
        if first_seen is None or last_bad_at is None:
            raise ValueError("incident timestamps must be valid")
        last_bad_report_at = parse_last_seen(data.get("last_bad_report_at"))
        last_recovery_report_at = parse_last_seen(data.get("last_recovery_report_at"))
        raw_findings = data.get("findings", [])
        if not isinstance(raw_findings, list):
            raise TypeError("findings must be a list")
        findings = tuple(str(item) for item in raw_findings if str(item))
        clean_reports = int(data.get("clean_reports", 0))
        if clean_reports < 0 or clean_reports >= INTEGRITY_RECOVERY_REPORTS_REQUIRED:
            raise ValueError("clean_reports outside active-incident range")
        return cls(
            first_seen=first_seen,
            last_bad_at=last_bad_at,
            last_bad_report_at=last_bad_report_at,
            findings=findings,
            clean_reports=clean_reports,
            last_recovery_report_at=last_recovery_report_at,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics."""
        return {
            "active": True,
            "first_seen": self.first_seen.isoformat(),
            "last_bad_at": self.last_bad_at.isoformat(),
            "last_bad_report_at": (
                self.last_bad_report_at.isoformat()
                if self.last_bad_report_at is not None
                else None
            ),
            "findings": list(self.findings),
            "clean_reports": self.clean_reports,
            "recovery_reports_required": INTEGRITY_RECOVERY_REPORTS_REQUIRED,
            "last_recovery_report_at": (
                self.last_recovery_report_at.isoformat()
                if self.last_recovery_report_at is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class IntegrityIncidentPlan:
    """Pure incident-latch transition."""

    record: IntegrityIncidentRecord | None
    effective_assessment: TelemetryIntegrityAssessment
    state: str
    changed: bool


def _ordered_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        for item in group:
            if item not in values:
                values.append(item)
    return tuple(values)


def _latched_assessment(
    raw: TelemetryIntegrityAssessment,
    record: IntegrityIncidentRecord,
) -> TelemetryIntegrityAssessment:
    """Force an active incident to remain service-required."""
    return TelemetryIntegrityAssessment(
        state="service_required",
        battery_trust=raw.battery_trust,
        voltage_trust=raw.voltage_trust,
        temperature_trust=raw.temperature_trust,
        outage_trust=raw.outage_trust,
        findings=_ordered_union(
            record.findings,
            raw.findings,
            ("service_incident_latched",),
        ),
        limitations=_ordered_union(
            raw.limitations,
            ("physical_battery_inspection_required",),
        ),
    )


def _recovery_eligible(assessment: TelemetryIntegrityAssessment) -> bool:
    """Return whether one genuinely new report can advance recovery."""
    if assessment.state == "trusted":
        return True
    findings = frozenset(assessment.findings)
    return (
        assessment.state == "guarded"
        and bool(findings)
        and findings.issubset(_RECOVERY_TOLERATED_FINDINGS)
        and "outage_counter_jump_retained_7d" in findings
    )


def _report_is_newer(
    report_at: datetime | None,
    *previous: datetime | None,
) -> bool:
    if report_at is None:
        return False
    known = [value for value in previous if value is not None]
    return not known or report_at > max(known)


def plan_integrity_incident(
    existing: IntegrityIncidentRecord | None,
    raw: TelemetryIntegrityAssessment,
    report_at: datetime | None,
    observed_at: datetime,
) -> IntegrityIncidentPlan:
    """Latch severe incidents until three distinct clean device reports arrive."""
    if existing is None:
        if raw.state != "service_required":
            return IntegrityIncidentPlan(None, raw, "inactive", False)
        record = IntegrityIncidentRecord(
            first_seen=observed_at,
            last_bad_at=observed_at,
            last_bad_report_at=report_at,
            findings=raw.findings,
            clean_reports=0,
            last_recovery_report_at=report_at,
        )
        return IntegrityIncidentPlan(record, raw, "created", True)

    if raw.state == "service_required":
        merged_findings = _ordered_union(existing.findings, raw.findings)
        new_bad_report = _report_is_newer(
            report_at,
            existing.last_bad_report_at,
            existing.last_recovery_report_at,
        )
        record = IntegrityIncidentRecord(
            first_seen=existing.first_seen,
            last_bad_at=observed_at if new_bad_report else existing.last_bad_at,
            last_bad_report_at=(
                report_at if new_bad_report else existing.last_bad_report_at
            ),
            findings=merged_findings,
            clean_reports=0 if new_bad_report else existing.clean_reports,
            last_recovery_report_at=(
                report_at if new_bad_report else existing.last_recovery_report_at
            ),
        )
        changed = record != existing
        return IntegrityIncidentPlan(
            record,
            _latched_assessment(raw, record),
            "refreshed" if changed else "latched",
            changed,
        )

    new_report = _report_is_newer(
        report_at,
        existing.last_bad_report_at,
        existing.last_recovery_report_at,
    )
    if not new_report:
        return IntegrityIncidentPlan(
            existing,
            _latched_assessment(raw, existing),
            "latched",
            False,
        )

    if _recovery_eligible(raw):
        clean_reports = existing.clean_reports + 1
        if clean_reports >= INTEGRITY_RECOVERY_REPORTS_REQUIRED:
            return IntegrityIncidentPlan(None, raw, "cleared", True)
        record = IntegrityIncidentRecord(
            first_seen=existing.first_seen,
            last_bad_at=existing.last_bad_at,
            last_bad_report_at=existing.last_bad_report_at,
            findings=existing.findings,
            clean_reports=clean_reports,
            last_recovery_report_at=report_at,
        )
        return IntegrityIncidentPlan(
            record,
            _latched_assessment(raw, record),
            "recovery_progress",
            True,
        )

    record = IntegrityIncidentRecord(
        first_seen=existing.first_seen,
        last_bad_at=existing.last_bad_at,
        last_bad_report_at=existing.last_bad_report_at,
        findings=existing.findings,
        clean_reports=0,
        last_recovery_report_at=report_at,
    )
    return IntegrityIncidentPlan(
        record,
        _latched_assessment(raw, record),
        "recovery_reset",
        record != existing,
    )


class IntegrityIncidentStore:
    """Persist active telemetry-integrity incidents by stable HA device id."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](
            hass,
            INTEGRITY_INCIDENT_STORAGE_VERSION,
            INTEGRITY_INCIDENT_STORAGE_KEY,
        )
        self.records: dict[str, IntegrityIncidentRecord] = {}
        self._dirty = False

    @property
    def dirty(self) -> bool:
        """Return whether the Store has unapplied in-memory changes."""
        return self._dirty

    async def async_load(self) -> None:
        """Load and validate active incident records."""
        stored = await self._store.async_load() or {}
        records = stored.get("devices", {})
        if not isinstance(records, dict):
            return
        for device_id, data in records.items():
            if not isinstance(device_id, str) or not isinstance(data, dict):
                continue
            try:
                self.records[device_id] = IntegrityIncidentRecord.from_storage_dict(data)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid integrity incident for %s", device_id)
        self._dirty = False

    def apply(
        self,
        device_id: str,
        raw: TelemetryIntegrityAssessment,
        report_at: datetime | None,
        observed_at: datetime,
    ) -> IntegrityIncidentPlan:
        """Apply one incident-latch transition to the in-memory Store."""
        result = plan_integrity_incident(
            self.records.get(device_id),
            raw,
            report_at,
            observed_at,
        )
        if result.changed:
            if result.record is None:
                self.records.pop(device_id, None)
            else:
                self.records[device_id] = result.record
            self._dirty = True
        return result

    def effective_assessment(
        self,
        device_id: str,
        raw: TelemetryIntegrityAssessment,
    ) -> TelemetryIntegrityAssessment:
        """Apply an existing latch without advancing recovery state."""
        record = self.records.get(device_id)
        if record is None:
            return raw
        return _latched_assessment(raw, record)

    async def async_save(self) -> None:
        """Persist the compact active-incident snapshot."""
        if not self._dirty:
            return
        await self._store.async_save(
            {
                "devices": {
                    device_id: record.as_storage_dict()
                    for device_id, record in self.records.items()
                }
            }
        )
        self._dirty = False
