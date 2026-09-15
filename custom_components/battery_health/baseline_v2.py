"""Guarded baseline v2 assessment helpers for Battery Health Analyzer."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from .cycle import CycleIntegrity, assess_cycle_integrity
from .evidence import EvidenceModel, VoltageInformation
from .models import BatteryHistorySummary, VoltageHistorySummary

MIN_SEGMENT_DAYS = 3
TARGET_SEGMENT_DAYS = 7
BOOTSTRAP_BATTERY_ANCHOR_PERCENT = 80.0
ELIGIBLE_CONFIDENCE = 0.60


@dataclass(frozen=True, slots=True)
class CycleSegment:
    state: str
    boundary_kind: str | None
    boundary_date: str | None
    cycle_start_known: bool
    usable_days: tuple[str, ...]
    excluded_days: int
    reasons: tuple[str, ...] = ()

    @property
    def complete_days(self) -> int:
        return len(self.usable_days)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "boundary_kind": self.boundary_kind,
            "boundary_date": self.boundary_date,
            "cycle_start_known": self.cycle_start_known,
            "complete_days": self.complete_days,
            "excluded_days": self.excluded_days,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class BaselineV2Assessment:
    eligibility: str
    confidence: float
    candidate_mv: float | None
    candidate_source: str | None
    anchor: str
    voltage_days: int
    coverage: float | None
    cycle_segment: CycleSegment
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "guarded_assessment",
            "eligibility": self.eligibility,
            "confidence": round(self.confidence, 3),
            "candidate": {
                "voltage_mv": round(self.candidate_mv) if self.candidate_mv is not None else None,
                "source": self.candidate_source,
                "persisted": False,
            },
            "anchor": self.anchor,
            "voltage_days": self.voltage_days,
            "coverage": round(self.coverage, 3) if self.coverage is not None else None,
            "cycle_segment": self.cycle_segment.as_dict(),
            "limitations": list(self.limitations),
        }


def _persisted_boundary(
    result: CycleIntegrity,
    next_day: str,
    battery_daily: Mapping[str, BatteryHistorySummary],
    voltage_daily: Mapping[str, VoltageHistorySummary],
) -> bool:
    battery_ok = result.battery_signal != "persistent_upshift"
    if not battery_ok:
        summary = battery_daily.get(next_day)
        battery_ok = bool(
            summary is not None
            and summary.issue is None
            and summary.median_percent is not None
            and result.battery_reference_percent is not None
            and summary.median_percent >= result.battery_reference_percent + 20.0
        )
    voltage_ok = result.voltage_signal != "persistent_upshift"
    if not voltage_ok:
        summary = voltage_daily.get(next_day)
        voltage_ok = bool(
            summary is not None
            and summary.issue is None
            and summary.median_mv is not None
            and summary.p10_mv is not None
            and result.voltage_reference_mv is not None
            and summary.median_mv >= result.voltage_reference_mv + 150.0
            and summary.p10_mv >= result.voltage_reference_mv + 75.0
        )
    return battery_ok and voltage_ok


def _latest_boundary_cluster(
    candidates: list[tuple[int, str, str]],
) -> tuple[int, str, str] | None:
    """Collapse consecutive detections from one physical replacement event."""
    if not candidates:
        return None
    clusters: list[list[tuple[int, str, str]]] = []
    for candidate in candidates:
        if not clusters or candidate[0] - clusters[-1][-1][0] > 2:
            clusters.append([candidate])
        else:
            clusters[-1].append(candidate)
    cluster = clusters[-1]
    first_index, first_day, _ = cluster[0]
    kind = (
        "probable_boundary"
        if any(item[2] == "probable_boundary" for item in cluster)
        else "possible_boundary"
    )
    return first_index, first_day, kind


def segment_current_cycle(
    battery_daily: Mapping[str, BatteryHistorySummary],
    voltage_daily: Mapping[str, VoltageHistorySummary],
    cycle_integrity: CycleIntegrity,
    voltage_information: VoltageInformation,
    battery_voltage_topology: str,
) -> CycleSegment:
    """Return only complete-day history that is safe for the current cycle."""
    days = sorted(set(battery_daily) | set(voltage_daily))
    total = len(days)
    if cycle_integrity.state == "insufficient":
        return CycleSegment("insufficient", None, None, False, (), total, ("cycle_integrity_insufficient",))
    if cycle_integrity.state in {"probable_boundary", "possible_boundary"}:
        return CycleSegment(
            "current_boundary",
            cycle_integrity.state,
            None,
            False,
            (),
            total,
            ("current_24h_boundary",),
        )

    candidates: list[tuple[int, str, str]] = []
    for index in range(3, len(days) - 1):
        day = days[index]
        previous = days[max(0, index - 7):index]
        result = assess_cycle_integrity(
            {key: battery_daily[key] for key in previous if key in battery_daily},
            {key: voltage_daily[key] for key in previous if key in voltage_daily},
            battery_daily.get(day),
            voltage_daily.get(day),
            voltage_information,
            "fresh",
            battery_voltage_topology,
        )
        if result.state not in {"probable_boundary", "possible_boundary"}:
            continue
        if _persisted_boundary(result, days[index + 1], battery_daily, voltage_daily):
            candidates.append((index, day, result.state))

    found = _latest_boundary_cluster(candidates)
    if found is None:
        return CycleSegment("left_censored", None, None, False, tuple(days), 0, ("no_boundary_detected_in_window",))
    index, boundary_date, kind = found
    if kind == "possible_boundary":
        return CycleSegment(
            "possible_boundary", kind, boundary_date, False, (), total,
            ("historical_boundary_not_strong_enough",),
        )
    return CycleSegment(
        "segmented",
        kind,
        boundary_date,
        False,
        tuple(days[index + 1:]),
        index + 1,
        ("historical_probable_boundary", "boundary_day_excluded"),
    )


def _upper(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values, reverse=True)
    return float(median(ordered[: min(3, len(ordered))]))


def _anchor(
    segment: CycleSegment,
    battery_daily: Mapping[str, BatteryHistorySummary],
    battery_processing: str,
) -> tuple[str, float]:
    if segment.state == "segmented" and segment.boundary_kind == "probable_boundary":
        return "observed_cycle_boundary", 1.0
    values = [
        float(battery_daily[day].p90_percent)
        for day in segment.usable_days
        if day in battery_daily
        and battery_daily[day].issue is None
        and battery_daily[day].p90_percent is not None
    ]
    upper = _upper(values)
    if upper is None or upper < BOOTSTRAP_BATTERY_ANCHOR_PERCENT:
        return "none", 0.0
    if battery_processing == "upper_envelope":
        return "battery_upper_bootstrap_volatile", 0.5
    return "battery_upper_bootstrap", 0.65


def assess_guarded_baseline_v2(
    battery_daily: Mapping[str, BatteryHistorySummary],
    voltage_daily: Mapping[str, VoltageHistorySummary],
    voltage_current: VoltageHistorySummary | None,
    evidence_model: EvidenceModel,
    cycle_integrity: CycleIntegrity,
    voltage_information: VoltageInformation,
) -> BaselineV2Assessment:
    """Assess guarded baseline eligibility without performing Store I/O."""
    segment = segment_current_cycle(
        battery_daily,
        voltage_daily,
        cycle_integrity,
        voltage_information,
        evidence_model.battery_voltage_topology,
    )
    informative_voltage = (
        evidence_model.voltage_role in {"primary", "supporting", "shared"}
        and voltage_information.information in {"continuous", "quantized"}
    )
    if not informative_voltage:
        return BaselineV2Assessment(
            "not_required", 0.0, None, None, "none", 0, None, segment,
            ("informative_voltage_baseline_not_required",),
        )
    if evidence_model.decision_readiness == "blocked" or segment.state in {"insufficient", "possible_boundary"}:
        reason = "evidence_blocked" if evidence_model.decision_readiness == "blocked" else "cycle_segment_blocked"
        return BaselineV2Assessment("blocked", 0.0, None, None, "none", 0, None, segment, (reason,))
    if evidence_model.temperature_context == "required":
        return BaselineV2Assessment(
            "blocked", 0.0, None, None, "none", 0, None, segment,
            ("temperature_context_required",),
        )

    if segment.state == "current_boundary":
        if segment.boundary_kind == "possible_boundary":
            return BaselineV2Assessment(
                "learning", 0.0, None, None, "possible_cycle_boundary", 0, None, segment,
                ("awaiting_boundary_confirmation",),
            )
        candidate = (
            voltage_current.p90_mv
            if voltage_current is not None and voltage_current.issue is None
            else None
        )
        coverage = voltage_current.coverage_ratio if voltage_current is not None and voltage_current.issue is None else None
        confidence = min(0.4, voltage_information.confidence, coverage or 0.0)
        return BaselineV2Assessment(
            "learning", confidence, candidate,
            "current_24h_after_boundary" if candidate is not None else None,
            "observed_cycle_boundary", 0, coverage, segment,
            ("no_complete_post_boundary_day",),
        )

    usable = [
        voltage_daily[day]
        for day in segment.usable_days
        if day in voltage_daily
        and voltage_daily[day].issue is None
        and voltage_daily[day].p90_mv is not None
    ]
    candidate = _upper([float(summary.p90_mv) for summary in usable if summary.p90_mv is not None])
    coverage = min((summary.coverage_ratio for summary in usable), default=None)
    voltage_days = len(usable)
    anchor, anchor_confidence = _anchor(segment, battery_daily, evidence_model.battery_processing)
    channel_confidence = min(voltage_information.confidence, 0.8) if voltage_information.information == "quantized" else voltage_information.confidence
    confidence = min(
        {"ready": 1.0, "limited": 0.5}.get(evidence_model.decision_readiness, 0.0),
        1.0 if segment.state == "segmented" else 0.65,
        anchor_confidence,
        min(1.0, voltage_days / TARGET_SEGMENT_DAYS),
        channel_confidence,
        coverage or 0.0,
    )
    limitations: list[str] = []
    if candidate is None:
        limitations.append("missing_segment_voltage")
    if voltage_days < MIN_SEGMENT_DAYS:
        limitations.append("insufficient_complete_cycle_days")
    if anchor == "none":
        limitations.append("healthy_anchor_missing")
    if evidence_model.decision_readiness == "limited":
        limitations.append("evidence_limited")
    eligible = (
        candidate is not None
        and voltage_days >= MIN_SEGMENT_DAYS
        and anchor != "none"
        and evidence_model.decision_readiness == "ready"
        and confidence >= ELIGIBLE_CONFIDENCE
    )
    return BaselineV2Assessment(
        "eligible" if eligible else "learning",
        confidence,
        candidate,
        "cycle_segment_upper_envelope" if candidate is not None else None,
        anchor,
        voltage_days,
        coverage,
        segment,
        tuple(limitations),
    )
