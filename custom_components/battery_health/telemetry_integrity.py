"""Cross-signal telemetry integrity checks for battery health decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from statistics import median
from typing import Any

from .models import BatteryHistorySummary, VoltageHistorySummary
from .operability import OutageEvidence

INTEGRITY_STATES = ("trusted", "guarded", "service_required")
SIGNAL_TRUST_STATES = ("trusted", "suspect", "rejected", "unavailable")

TEMPERATURE_SENTINEL_LOW_C = -300.0
TEMPERATURE_SENTINEL_HIGH_C = 300.0
OUTAGE_BURST_EVENTS_24H = 10
OUTAGE_IMPLAUSIBLE_DELTA = 1024
BATTERY_COLLAPSE_MAX_PERCENT = 10.0
BATTERY_REFERENCE_MIN_PERCENT = 50.0
BATTERY_COLLAPSE_DROP_PP = 40.0
VOLTAGE_CONTRADICTION_MIN_RATIO = 0.98


@dataclass(frozen=True, slots=True)
class TelemetryIntegrityAssessment:
    """Trust decision for the telemetry used by the health model."""

    state: str
    battery_trust: str
    voltage_trust: str
    temperature_trust: str
    outage_trust: str
    findings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics for the disabled deep-diagnostic sensor."""
        return {
            "state": self.state,
            "battery_trust": self.battery_trust,
            "voltage_trust": self.voltage_trust,
            "temperature_trust": self.temperature_trust,
            "outage_trust": self.outage_trust,
            "findings": list(self.findings),
            "limitations": list(self.limitations),
        }


def _upper_reference(values: list[float]) -> float | None:
    """Return the median of the highest up-to-three complete-day values."""
    if not values:
        return None
    ordered = sorted(values, reverse=True)
    return float(median(ordered[: min(3, len(ordered))]))


def _battery_reference(
    daily: Mapping[str, BatteryHistorySummary],
) -> float | None:
    values = [
        float(summary.p90_percent)
        for summary in daily.values()
        if summary.issue is None and summary.p90_percent is not None
    ]
    return _upper_reference(values)


def _voltage_reference(
    daily: Mapping[str, VoltageHistorySummary],
) -> float | None:
    values = [
        float(summary.p90_mv)
        for summary in daily.values()
        if summary.issue is None and summary.p90_mv is not None
    ]
    return _upper_reference(values)


def assess_telemetry_integrity(
    battery_current: BatteryHistorySummary | None,
    voltage_current: VoltageHistorySummary | None,
    battery_daily: Mapping[str, BatteryHistorySummary],
    voltage_daily: Mapping[str, VoltageHistorySummary],
    outage: OutageEvidence | None,
    current_temperature_c: float | None,
) -> TelemetryIntegrityAssessment:
    """Classify contradictory or implausible telemetry before health scoring."""
    findings: list[str] = []
    limitations: list[str] = []

    battery_trust = "unavailable" if battery_current is None else "trusted"
    voltage_trust = "unavailable" if voltage_current is None else "trusted"
    temperature_trust = (
        "unavailable" if current_temperature_c is None else "trusted"
    )
    outage_trust = (
        "unavailable" if outage is None or not outage.supported else "trusted"
    )

    temperature_sentinel = (
        current_temperature_c is not None
        and (
            current_temperature_c <= TEMPERATURE_SENTINEL_LOW_C
            or current_temperature_c >= TEMPERATURE_SENTINEL_HIGH_C
        )
    )
    if temperature_sentinel:
        findings.append("temperature_protocol_sentinel")
        temperature_trust = "rejected"

    outage_huge_jump = False
    outage_burst = False
    if outage is not None and outage.supported:
        recent_max_delta = outage.max_positive_delta_24h
        retained_max_delta = outage.max_positive_delta_7d
        max_delta = max(
            (
                value
                for value in (recent_max_delta, retained_max_delta)
                if value is not None
            ),
            default=None,
        )
        outage_huge_jump = (
            max_delta is not None and max_delta >= OUTAGE_IMPLAUSIBLE_DELTA
        )
        outage_burst = (
            not outage_huge_jump
            and outage.events_24h is not None
            and outage.events_24h >= OUTAGE_BURST_EVENTS_24H
        )
        if outage_huge_jump:
            findings.append("outage_counter_implausible_jump")
            if (
                (recent_max_delta is None or recent_max_delta < OUTAGE_IMPLAUSIBLE_DELTA)
                and retained_max_delta is not None
                and retained_max_delta >= OUTAGE_IMPLAUSIBLE_DELTA
            ):
                findings.append("outage_counter_jump_retained_7d")
            outage_trust = "rejected"
        elif outage_burst:
            findings.append("outage_counter_burst")
            outage_trust = "suspect"

    battery_now = (
        battery_current.latest_percent
        if battery_current is not None and battery_current.issue is None
        else None
    )
    if battery_now is None and battery_current is not None:
        battery_now = battery_current.p90_percent
    battery_ref = _battery_reference(battery_daily)

    battery_collapse = (
        battery_now is not None
        and battery_ref is not None
        and battery_now <= BATTERY_COLLAPSE_MAX_PERCENT
        and battery_ref >= BATTERY_REFERENCE_MIN_PERCENT
        and battery_ref - battery_now >= BATTERY_COLLAPSE_DROP_PP
    )
    if battery_collapse:
        findings.append("battery_abrupt_collapse")
        battery_trust = "suspect"

    voltage_now = (
        voltage_current.latest_voltage_mv
        if voltage_current is not None and voltage_current.issue is None
        else None
    )
    if voltage_now is None and voltage_current is not None:
        voltage_now = voltage_current.p90_mv
    voltage_ref = _voltage_reference(voltage_daily)

    voltage_contradiction = (
        battery_collapse
        and voltage_now is not None
        and voltage_ref is not None
        and voltage_ref > 0
        and voltage_now / voltage_ref >= VOLTAGE_CONTRADICTION_MIN_RATIO
    )
    if voltage_contradiction:
        findings.append("battery_voltage_contradiction")

    hard_metadata_findings = int(temperature_sentinel) + int(outage_huge_jump)
    service_required = (
        hard_metadata_findings >= 2
        or (
            battery_collapse
            and (temperature_sentinel or outage_huge_jump)
        )
    )

    if service_required:
        state = "service_required"
        if battery_collapse:
            battery_trust = "rejected"
        limitations.append("physical_battery_inspection_required")
    elif findings:
        state = "guarded"
        limitations.append("telemetry_integrity_guarded")
    else:
        state = "trusted"

    return TelemetryIntegrityAssessment(
        state=state,
        battery_trust=battery_trust,
        voltage_trust=voltage_trust,
        temperature_trust=temperature_trust,
        outage_trust=outage_trust,
        findings=tuple(findings),
        limitations=tuple(limitations),
    )
