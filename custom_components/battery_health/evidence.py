"""Pure evidence-routing helpers for Battery Health Analyzer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .models import BatteryHistorySummary, TelemetryProfile, VoltageHistorySummary
from .operability import FreshnessEvidence, OutageEvidence

VOLTAGE_INFORMATION_MIN_DAYS = 5
VOLTAGE_STATIC_SPAN_MV = 1.0
VOLTAGE_QUANTIZED_MAX_LEVELS = 6
VOLTAGE_QUANTIZED_MIN_STEP_MV = 25.0


@dataclass(frozen=True, slots=True)
class VoltageInformation:
    """Describe how much information a voltage channel actually carries."""

    information: str
    confidence: float
    valid_days: int
    distinct_levels: int
    span_mv: float | None
    min_step_mv: float | None

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics."""
        return {
            "type": self.information,
            "confidence": round(self.confidence, 3),
            "valid_days": self.valid_days,
            "distinct_levels": self.distinct_levels,
            "span_mv": round(self.span_mv, 1) if self.span_mv is not None else None,
            "min_step_mv": (
                round(self.min_step_mv, 1) if self.min_step_mv is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class EvidenceModel:
    """Describe how available telemetry may be used without issuing a verdict."""

    freshness_gate: str
    decision_readiness: str
    battery_role: str
    battery_processing: str
    voltage_role: str
    voltage_information: VoltageInformation
    battery_voltage_topology: str
    temperature_context: str
    outage_role: str
    independent_condition_channels: int
    double_count_guard: bool
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a compact diagnostic representation."""
        return {
            "freshness_gate": self.freshness_gate,
            "decision_readiness": self.decision_readiness,
            "condition_channels": {
                "independent_count": self.independent_condition_channels,
                "double_count_guard": self.double_count_guard,
            },
            "battery": {
                "role": self.battery_role,
                "processing": self.battery_processing,
            },
            "voltage": {
                "role": self.voltage_role,
                "information": self.voltage_information.as_dict(),
            },
            "battery_voltage_topology": self.battery_voltage_topology,
            "temperature_context": self.temperature_context,
            "outage_role": self.outage_role,
            "limitations": list(self.limitations),
        }


def classify_voltage_information(
    voltage_daily: Mapping[str, VoltageHistorySummary],
) -> VoltageInformation:
    """Classify voltage telemetry as continuous, quantized, static or insufficient."""
    valid = [
        summary
        for _, summary in sorted(voltage_daily.items())
        if summary.median_mv is not None and summary.issue is None
    ]
    valid_days = len(valid)
    if valid_days < VOLTAGE_INFORMATION_MIN_DAYS:
        return VoltageInformation("insufficient", 0.0, valid_days, 0, None, None)

    values: list[float] = []
    for summary in valid:
        for value in (
            summary.p10_mv,
            summary.median_mv,
            summary.p90_mv,
            summary.min_mv,
            summary.max_mv,
        ):
            if value is not None:
                values.append(float(value))

    if not values:
        return VoltageInformation("insufficient", 0.0, valid_days, 0, None, None)

    rounded_levels = sorted({round(value, 1) for value in values})
    span = max(values) - min(values)
    distinct_levels = len(rounded_levels)
    steps = [
        right - left
        for left, right in zip(rounded_levels, rounded_levels[1:], strict=False)
        if right > left
    ]
    min_step = min(steps) if steps else None

    if span <= VOLTAGE_STATIC_SPAN_MV or distinct_levels == 1:
        return VoltageInformation(
            "static",
            1.0,
            valid_days,
            distinct_levels,
            span,
            min_step,
        )

    if (
        2 <= distinct_levels <= VOLTAGE_QUANTIZED_MAX_LEVELS
        and min_step is not None
        and min_step >= VOLTAGE_QUANTIZED_MIN_STEP_MV
    ):
        confidence = min(1.0, 0.75 + valid_days / 120)
        return VoltageInformation(
            "quantized",
            confidence,
            valid_days,
            distinct_levels,
            span,
            min_step,
        )

    confidence = min(1.0, 0.7 + valid_days / 100)
    return VoltageInformation(
        "continuous",
        confidence,
        valid_days,
        distinct_levels,
        span,
        min_step,
    )


def _freshness_gate(freshness: FreshnessEvidence | None) -> str:
    if freshness is None:
        return "blocked"
    return {
        "fresh": "open",
        "late": "caution",
        "stale": "blocked",
        "insufficient": "limited",
        "invalid": "blocked",
        "unavailable": "blocked",
    }.get(freshness.state, "blocked")


def _battery_processing(behavior: str) -> str:
    return {
        "volatile": "upper_envelope",
        "monotonic": "trend",
        "static": "level",
        "mixed": "trend_and_level",
        "insufficient": "limited",
    }.get(behavior, "limited")


def _battery_voltage_topology(relation: str) -> tuple[str, bool]:
    if relation == "derived":
        return "shared", True
    if relation in {"strong", "moderate"}:
        return "coupled", True
    if relation == "weak":
        return "independent", False
    return "unknown", True


def _temperature_context(relation: str) -> str:
    if relation.startswith("strong_"):
        return "required"
    if relation.startswith("moderate_"):
        return "relevant"
    if relation.startswith("weak_"):
        return "optional"
    if relation == "unavailable":
        return "unavailable"
    return "unknown"


def _outage_role(outage: OutageEvidence | None) -> str:
    if outage is None or not outage.supported:
        return "unavailable"
    if outage.events_24h is None:
        return "unknown"
    return "escalating_support" if outage.events_24h > 0 else "neutral"


def build_evidence_model(
    profile: TelemetryProfile,
    battery: BatteryHistorySummary | None,
    voltage: VoltageHistorySummary | None,
    freshness: FreshnessEvidence | None,
    outage: OutageEvidence | None,
    voltage_information: VoltageInformation,
) -> EvidenceModel:
    """Route telemetry channels conservatively without producing health state."""
    gate = _freshness_gate(freshness)
    topology, double_count_guard = _battery_voltage_topology(
        profile.battery_voltage_relation.relation
    )

    battery_available = (
        battery is not None
        and battery.median_percent is not None
        and battery.issue is None
    )
    voltage_available = (
        voltage is not None and voltage.median_mv is not None and voltage.issue is None
    )

    battery_processing = _battery_processing(profile.battery_behavior.behavior)
    battery_role = "primary" if battery_available else "unavailable"

    information = voltage_information.information
    if not voltage_available:
        voltage_role = "unavailable"
    elif information == "static":
        voltage_role = "context_only"
    elif information == "quantized":
        voltage_role = "supporting"
    elif information == "continuous":
        voltage_role = "primary"
    else:
        voltage_role = "limited"

    if battery_available and voltage_available and topology == "shared":
        battery_role = "shared"
        if voltage_role in {"primary", "supporting"}:
            voltage_role = "shared"
    elif battery_available and voltage_available and topology == "coupled":
        if voltage_role == "primary":
            voltage_role = "supporting"

    independent_channels = 1 if battery_available else 0
    if (
        voltage_available
        and voltage_role not in {"context_only", "limited", "unavailable"}
        and topology == "independent"
    ):
        independent_channels += 1
    elif (
        independent_channels == 0
        and voltage_available
        and voltage_role != "unavailable"
    ):
        independent_channels = 1

    limitations: list[str] = []
    if gate == "limited":
        limitations.append("freshness_not_learned")
    elif gate == "blocked":
        limitations.append("freshness_blocked")
    if not battery_available:
        limitations.append("battery_unavailable")
    if not voltage_available:
        limitations.append("voltage_unavailable")
    elif information == "static":
        limitations.append("voltage_static")
    elif information == "quantized":
        limitations.append("voltage_quantized")
    elif information == "insufficient":
        limitations.append("voltage_information_insufficient")
    if topology == "unknown" and battery_available and voltage_available:
        limitations.append("battery_voltage_independence_unknown")
    if profile.battery_behavior.behavior == "insufficient":
        limitations.append("battery_behavior_insufficient")

    if gate == "blocked" or independent_channels == 0:
        readiness = "blocked"
    elif gate == "limited" or profile.battery_behavior.behavior == "insufficient":
        readiness = "limited"
    else:
        readiness = "ready"

    return EvidenceModel(
        freshness_gate=gate,
        decision_readiness=readiness,
        battery_role=battery_role,
        battery_processing=battery_processing,
        voltage_role=voltage_role,
        voltage_information=voltage_information,
        battery_voltage_topology=topology,
        temperature_context=_temperature_context(
            profile.voltage_temperature_relation.relation
        ),
        outage_role=_outage_role(outage),
        independent_condition_channels=independent_channels,
        double_count_guard=double_count_guard,
        limitations=tuple(limitations),
    )
