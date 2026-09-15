"""Pure health assessment for Battery Health Analyzer.

The classifier remains side-effect free. Dev23 publishes the already-validated
assessment through Home Assistant entities, while persistence and cycle state stay
owned by their dedicated layers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .baseline_v2 import BaselineV2Assessment
from .cycle import CycleIntegrity
from .evidence import EvidenceModel
from .models import BatteryHistorySummary, TelemetryProfile, VoltageHistorySummary

HEALTH_STATES = ("ok", "weakening", "replace", "unknown")
VOLTAGE_OK_RATIO = 0.91
VOLTAGE_REPLACE_RATIO = 0.87
HEALTH_MIN_COVERAGE = 0.80
BATTERY_ONLY_MIN_DAYS = 7
BATTERY_ONLY_MIN_BEHAVIOR_CONFIDENCE = 0.50
BATTERY_ONLY_OK_MIN_PERCENT = 75.0
BATTERY_ONLY_LOW_MAX_PERCENT = 30.0
BATTERY_ONLY_LOW_UPPER_MAX_PERCENT = 35.0
BATTERY_ONLY_MATERIAL_DROP_PP = 15.0


@dataclass(frozen=True, slots=True)
class ShadowHealthAssessment:
    """One conservative health decision produced by the validated classifier."""

    candidate_state: str
    confidence: float
    decision_path: str
    voltage_health_ratio: float | None
    battery_level_percent: float | None
    baseline_mv: float | None
    current_voltage_p90_mv: float | None
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return transparent diagnostics for calibration and production parity."""
        return {
            "mode": "published_classifier",
            "candidate_state": self.candidate_state,
            "confidence": round(self.confidence, 3),
            "decision_path": self.decision_path,
            "metrics": {
                "voltage_health_ratio": (
                    round(self.voltage_health_ratio, 3)
                    if self.voltage_health_ratio is not None
                    else None
                ),
                "battery_level_percent": (
                    round(self.battery_level_percent, 1)
                    if self.battery_level_percent is not None
                    else None
                ),
                "baseline_mv": (
                    round(self.baseline_mv) if self.baseline_mv is not None else None
                ),
                "current_voltage_p90_mv": (
                    round(self.current_voltage_p90_mv)
                    if self.current_voltage_p90_mv is not None
                    else None
                ),
            },
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
        }


def summarize_health_states(states: Iterable[str]) -> tuple[str, dict[str, int]]:
    """Return actionable aggregate state plus normalized state counts.

    Actionable states outrank uncertainty: replace > weakening > unknown > ok.
    Unknown or unexpected input values are conservatively counted as unknown.
    """
    counts = {state: 0 for state in HEALTH_STATES}
    for state in states:
        normalized = state if state in counts else "unknown"
        counts[normalized] += 1

    if counts["replace"]:
        summary = "replace"
    elif counts["weakening"]:
        summary = "weakening"
    elif counts["unknown"]:
        summary = "unknown"
    elif counts["ok"]:
        summary = "ok"
    else:
        summary = "unknown"
    return summary, counts


def _unknown(
    reason: str,
    *,
    decision_path: str = "guarded_unknown",
    battery_level_percent: float | None = None,
    baseline_mv: float | None = None,
    current_voltage_p90_mv: float | None = None,
    voltage_health_ratio: float | None = None,
    limitations: tuple[str, ...] = (),
) -> ShadowHealthAssessment:
    return ShadowHealthAssessment(
        candidate_state="unknown",
        confidence=0.0,
        decision_path=decision_path,
        voltage_health_ratio=voltage_health_ratio,
        battery_level_percent=battery_level_percent,
        baseline_mv=baseline_mv,
        current_voltage_p90_mv=current_voltage_p90_mv,
        reasons=(reason,),
        limitations=limitations,
    )


def _battery_level(
    battery: BatteryHistorySummary | None,
    processing: str,
) -> float | None:
    if battery is None or battery.issue is not None:
        return None
    if processing == "upper_envelope":
        return battery.p90_percent
    return (
        battery.p90_percent
        if battery.p90_percent is not None
        else battery.median_percent
    )


def assess_shadow_health(
    battery: BatteryHistorySummary | None,
    voltage: VoltageHistorySummary | None,
    profile: TelemetryProfile,
    evidence_model: EvidenceModel,
    cycle_integrity: CycleIntegrity,
    baseline_v2: BaselineV2Assessment,
    *,
    persisted_baseline_mv: float | None,
    persisted_baseline_confidence: float | None,
) -> ShadowHealthAssessment:
    """Return a conservative health decision from already-guarded evidence.

    Voltage-baseline decisions use the current 24-hour p90 against the guarded
    persisted baseline. Battery-only evidence can identify a low-confidence OK
    or WEAKENING candidate, but intentionally cannot produce REPLACE by itself.
    """
    battery_level = _battery_level(battery, evidence_model.battery_processing)

    if evidence_model.decision_readiness != "ready":
        return _unknown(
            "evidence_not_ready",
            battery_level_percent=battery_level,
            limitations=tuple(evidence_model.limitations),
        )
    if evidence_model.freshness_gate not in {"open", "caution"}:
        return _unknown(
            "freshness_gate_not_open",
            battery_level_percent=battery_level,
        )
    if cycle_integrity.state != "stable":
        return _unknown(
            "cycle_integrity_not_stable",
            battery_level_percent=battery_level,
            limitations=(cycle_integrity.state,),
        )
    if baseline_v2.cycle_segment.state in {
        "possible_boundary",
        "current_boundary",
        "insufficient",
    }:
        return _unknown(
            "cycle_segment_not_safe",
            battery_level_percent=battery_level,
            limitations=(baseline_v2.cycle_segment.state,),
        )
    if evidence_model.temperature_context == "required":
        return _unknown(
            "temperature_context_required",
            battery_level_percent=battery_level,
        )

    # When a new historical boundary has been confirmed but the clean post-boundary
    # segment is not yet eligible, an existing Store record still belongs to the
    # previous cycle. Never compare the new cycle against that old baseline.
    if (
        baseline_v2.cycle_segment.state == "segmented"
        and baseline_v2.eligibility != "eligible"
    ):
        return _unknown(
            "new_cycle_baseline_not_ready",
            decision_path="awaiting_cycle_baseline",
            battery_level_percent=battery_level,
            baseline_mv=persisted_baseline_mv,
            limitations=("previous_cycle_baseline_not_usable",),
        )

    # A guarded persisted voltage baseline is the strongest currently calibrated
    # path. Shared/derived signals are still counted exactly once: voltage is the
    # calibrated representation and battery percentage is not added as a second
    # condition channel.
    if (
        persisted_baseline_mv is not None
        and persisted_baseline_mv > 0
        and persisted_baseline_confidence is not None
        and evidence_model.voltage_role in {"primary", "shared"}
    ):
        if (
            voltage is None
            or voltage.issue is not None
            or voltage.p90_mv is None
            or voltage.coverage_ratio < HEALTH_MIN_COVERAGE
        ):
            return _unknown(
                "current_voltage_not_usable",
                decision_path="voltage_baseline",
                battery_level_percent=battery_level,
                baseline_mv=persisted_baseline_mv,
            )

        ratio = float(voltage.p90_mv) / float(persisted_baseline_mv)
        if ratio >= VOLTAGE_OK_RATIO:
            state = "ok"
            reason = "voltage_ratio_ok"
        elif ratio >= VOLTAGE_REPLACE_RATIO:
            state = "weakening"
            reason = "voltage_ratio_weakening"
        else:
            state = "replace"
            reason = "voltage_ratio_replace"

        confidence = min(
            float(persisted_baseline_confidence),
            evidence_model.voltage_information.confidence,
            voltage.coverage_ratio,
        )
        if evidence_model.freshness_gate == "caution":
            confidence = min(confidence, 0.50)

        reasons = [reason]
        limitations: list[str] = []
        if evidence_model.double_count_guard:
            reasons.append("double_count_guard_applied")
        if evidence_model.outage_role == "escalating_support":
            reasons.append("outage_support_present")
            if state == "ok":
                return ShadowHealthAssessment(
                    candidate_state="unknown",
                    confidence=0.0,
                    decision_path="voltage_baseline",
                    voltage_health_ratio=ratio,
                    battery_level_percent=battery_level,
                    baseline_mv=persisted_baseline_mv,
                    current_voltage_p90_mv=voltage.p90_mv,
                    reasons=("outage_conflicts_with_ok",),
                    limitations=("outage_support_requires_calibration",),
                )
            confidence = min(0.70, confidence + 0.05)

        return ShadowHealthAssessment(
            candidate_state=state,
            confidence=confidence,
            decision_path="voltage_baseline",
            voltage_health_ratio=ratio,
            battery_level_percent=battery_level,
            baseline_mv=persisted_baseline_mv,
            current_voltage_p90_mv=voltage.p90_mv,
            reasons=tuple(reasons),
            limitations=tuple(limitations),
        )

    # A shared/derived signal without a guarded healthy baseline has no absolute
    # scale. Battery percentage and voltage are the same physical evidence, so
    # falling back to battery percentage here would only disguise missing
    # calibration.
    if evidence_model.battery_role == "shared":
        return _unknown(
            "shared_signal_without_persisted_baseline",
            decision_path="uncalibrated_shared_signal",
            battery_level_percent=battery_level,
            limitations=("healthy_anchor_missing",),
        )

    # Continuous primary voltage without a persisted baseline is also deliberately
    # not scored absolutely. Supporting/context-only voltage may still leave the
    # primary battery trend usable as a cautious fallback.
    if evidence_model.voltage_role == "primary" and persisted_baseline_mv is None:
        return _unknown(
            "primary_voltage_without_persisted_baseline",
            decision_path="uncalibrated_voltage",
            battery_level_percent=battery_level,
            limitations=("healthy_voltage_baseline_missing",),
        )

    if evidence_model.battery_role != "primary":
        return _unknown(
            "primary_battery_evidence_unavailable",
            decision_path="battery_only",
            battery_level_percent=battery_level,
        )
    if (
        battery is None
        or battery.issue is not None
        or battery_level is None
        or battery.coverage_ratio < HEALTH_MIN_COVERAGE
    ):
        return _unknown(
            "battery_history_not_usable",
            decision_path="battery_only",
            battery_level_percent=battery_level,
        )
    if profile.battery_days < BATTERY_ONLY_MIN_DAYS:
        return _unknown(
            "battery_history_too_short",
            decision_path="battery_only",
            battery_level_percent=battery_level,
        )
    if (
        profile.battery_behavior.confidence
        < BATTERY_ONLY_MIN_BEHAVIOR_CONFIDENCE
    ):
        return _unknown(
            "battery_behavior_low_confidence",
            decision_path="battery_only",
            battery_level_percent=battery_level,
        )

    confidence = min(
        0.55,
        battery.coverage_ratio,
        profile.battery_behavior.confidence,
    )
    if evidence_model.freshness_gate == "caution":
        confidence = min(confidence, 0.45)

    upper_7d = profile.battery_upper_7d_percent
    behavior = profile.battery_behavior.behavior
    reasons: list[str] = []
    limitations = ["battery_only_no_replace"]

    if battery_level >= BATTERY_ONLY_OK_MIN_PERCENT:
        if (
            upper_7d is not None
            and upper_7d - battery_level >= BATTERY_ONLY_MATERIAL_DROP_PP
            and behavior in {"monotonic", "mixed"}
        ):
            return _unknown(
                "high_battery_but_material_decline",
                decision_path="battery_only",
                battery_level_percent=battery_level,
                limitations=("trend_requires_more_calibration",),
            )
        reasons.append("battery_upper_level_high")
        if (
            upper_7d is not None
            and upper_7d > battery_level
            and behavior in {"monotonic", "mixed"}
        ):
            reasons.append("battery_declining_from_7d_upper")
            confidence = min(confidence, 0.45)
        if evidence_model.outage_role == "escalating_support":
            return _unknown(
                "outage_conflicts_with_battery_only_ok",
                decision_path="battery_only",
                battery_level_percent=battery_level,
                limitations=("outage_support_requires_calibration",),
            )
        return ShadowHealthAssessment(
            candidate_state="ok",
            confidence=confidence,
            decision_path="battery_only",
            voltage_health_ratio=None,
            battery_level_percent=battery_level,
            baseline_mv=None,
            current_voltage_p90_mv=(
                voltage.p90_mv
                if voltage is not None and voltage.issue is None
                else None
            ),
            reasons=tuple(reasons),
            limitations=tuple(limitations),
        )

    if (
        battery_level <= BATTERY_ONLY_LOW_MAX_PERCENT
        and upper_7d is not None
        and upper_7d <= BATTERY_ONLY_LOW_UPPER_MAX_PERCENT
        and behavior in {"monotonic", "mixed"}
    ):
        reasons.extend(
            (
                "battery_level_persistently_low",
                "battery_trend_supports_decline",
            )
        )
        if evidence_model.outage_role == "escalating_support":
            reasons.append("outage_support_present")
            confidence = min(0.65, confidence + 0.05)
        return ShadowHealthAssessment(
            candidate_state="weakening",
            confidence=confidence,
            decision_path="battery_only",
            voltage_health_ratio=None,
            battery_level_percent=battery_level,
            baseline_mv=None,
            current_voltage_p90_mv=(
                voltage.p90_mv
                if voltage is not None and voltage.issue is None
                else None
            ),
            reasons=tuple(reasons),
            limitations=tuple(limitations),
        )

    return _unknown(
        "battery_level_not_calibrated",
        decision_path="battery_only",
        battery_level_percent=battery_level,
        limitations=("battery_only_midrange_or_uncorroborated_low",),
    )
