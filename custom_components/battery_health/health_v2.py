"""Relative-history Health Model v2 for Battery Health Analyzer.

Dev24 keeps this model read-only and diagnostic.  The production dev23 classifier
remains unchanged until the relative model has been validated on the real MQTT
population.

The central design rule is that a battery condition is different from our ability
to measure it.  A calculable device therefore receives a condition state even
when confidence is limited; data quality is reported separately through
``calculation_state`` and ``confidence``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from .baseline_v2 import BaselineV2Assessment
from .cycle import CycleIntegrity
from .evidence import EvidenceModel
from .models import BatteryHistorySummary, VoltageHistorySummary

CONDITION_STATES = ("ok", "declining", "weakening", "replace")
CALCULATION_STATES = ("ready", "limited", "unavailable")
TREND_STATES = ("stable", "falling", "recovering", "volatile", "insufficient")

MIN_COVERAGE = 0.80
MIN_REFERENCE_DAYS = 3
READY_REFERENCE_DAYS = 7
REFERENCE_TOP_DAYS = 3
TREND_BLOCK_DAYS = 3

# Relative-voltage thresholds are deliberately tighter than the old absolute
# baseline thresholds because ``declining`` is informational, not an instruction
# to replace the battery.  Escalation still requires persistence.
VOLTAGE_DECLINING_RATIO = 0.98
VOLTAGE_WEAKENING_RATIO = 0.94
VOLTAGE_REPLACE_RATIO = 0.90
VOLTAGE_FALLING_BLOCK_RATIO = 0.99
VOLTAGE_RECOVERING_BLOCK_RATIO = 1.01

# Percentage is not assumed to be literal remaining capacity.  Changes are used
# relative to the device's own history and absolute level is only supporting
# evidence for the two most severe states.
BATTERY_DECLINING_DROP_PP = 3.0
BATTERY_STRONG_DECLINE_DROP_PP = 10.0
BATTERY_WEAKENING_DROP_PP = 20.0
BATTERY_LOW_PERCENT = 25.0
BATTERY_REPLACE_PERCENT = 10.0
BATTERY_REPLACE_7D_PERCENT = 15.0
BATTERY_FALLING_BLOCK_PP = 2.0
BATTERY_RECOVERING_BLOCK_PP = 2.0


@dataclass(frozen=True, slots=True)
class RelativeHealthAssessment:
    """One Health Model v2 assessment.

    ``condition_state`` is intentionally nullable only when neither voltage nor
    battery percentage has enough usable current data to make any calculation.
    That is a measurement-availability outcome, not a battery-health state.
    """

    condition_state: str | None
    calculation_state: str
    trend_state: str
    confidence: float
    assessment_mode: str
    signal: str | None
    current_value: float | None
    reference_7d: float | None
    reference_30d: float | None
    reference_used: float | None
    ratio_to_reference: float | None
    delta_from_reference: float | None
    recent_vs_previous: float | None
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return transparent shadow diagnostics."""

        def rounded(value: float | None, digits: int = 3) -> float | None:
            return round(value, digits) if value is not None else None

        return {
            "mode": "relative_health_v2_shadow",
            "condition_state": self.condition_state,
            "calculation_state": self.calculation_state,
            "trend_state": self.trend_state,
            "confidence": round(self.confidence, 3),
            "assessment_mode": self.assessment_mode,
            "signal": self.signal,
            "metrics": {
                "current_24h_robust": rounded(self.current_value, 1),
                "reference_7d": rounded(self.reference_7d, 1),
                "reference_30d": rounded(self.reference_30d, 1),
                "reference_used": rounded(self.reference_used, 1),
                "ratio_to_reference": rounded(self.ratio_to_reference, 3),
                "delta_from_reference": rounded(self.delta_from_reference, 1),
                "recent_vs_previous": rounded(self.recent_vs_previous, 3),
            },
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
        }


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _daily_battery_values(
    daily: Mapping[str, BatteryHistorySummary],
) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for day in sorted(daily):
        summary = daily[day]
        value = _finite(summary.p90_percent if summary.issue is None else None)
        if value is not None:
            result.append((day, value))
    return result


def _daily_voltage_values(
    daily: Mapping[str, VoltageHistorySummary],
) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for day in sorted(daily):
        summary = daily[day]
        value = _finite(summary.p90_mv if summary.issue is None else None)
        if value is not None:
            result.append((day, value))
    return result


def _upper_reference(values: Sequence[float]) -> float | None:
    """Return median of the highest up-to-three complete-day robust values."""
    if not values:
        return None
    ordered = sorted((float(value) for value in values), reverse=True)
    return float(median(ordered[: min(REFERENCE_TOP_DAYS, len(ordered))]))


def _window_reference(values: Sequence[float], days: int = 7) -> float | None:
    """Return robust recent upper envelope over the requested complete days."""
    if not values:
        return None
    return float(median(values[-days:]))


def _trend(
    values: Sequence[float],
    *,
    signal: str,
    long_term_behavior: str | None = None,
) -> tuple[str, float | None]:
    """Classify recent direction from adjacent three-day robust blocks."""
    if len(values) < TREND_BLOCK_DAYS * 2:
        if long_term_behavior == "volatile":
            return "volatile", None
        return "insufficient", None

    previous = float(median(values[-6:-3]))
    recent = float(median(values[-3:]))
    if signal == "voltage_mv":
        if previous <= 0:
            return "insufficient", None
        change = recent / previous
        if change <= VOLTAGE_FALLING_BLOCK_RATIO:
            return "falling", change
        if change >= VOLTAGE_RECOVERING_BLOCK_RATIO:
            return "recovering", change
        recent_span = max(values[-7:]) - min(values[-7:])
        if long_term_behavior == "volatile" and recent_span / previous >= 0.02:
            return "volatile", change
        return "stable", change

    change_pp = recent - previous
    if change_pp <= -BATTERY_FALLING_BLOCK_PP:
        return "falling", change_pp
    if change_pp >= BATTERY_RECOVERING_BLOCK_PP:
        return "recovering", change_pp
    if long_term_behavior == "volatile":
        return "volatile", change_pp
    return "stable", change_pp


def _calculation_quality(
    *,
    coverage: float,
    reference_days: int,
    confidence_cap: float,
    forced_limited: bool,
) -> tuple[str, float]:
    if coverage < MIN_COVERAGE:
        return "limited", min(0.35, max(0.0, coverage))
    if reference_days < MIN_REFERENCE_DAYS:
        return "limited", min(0.35, coverage, confidence_cap)
    state = "ready" if reference_days >= READY_REFERENCE_DAYS else "limited"
    if forced_limited:
        state = "limited"
    history_confidence = min(1.0, reference_days / 14.0)
    confidence = min(coverage, history_confidence, confidence_cap)
    if state == "limited":
        confidence = min(confidence, 0.50)
    return state, confidence


def _voltage_condition(
    *,
    current: float,
    reference: float,
    reference_7d: float | None,
    reference_30d: float | None,
    trend: str,
    conservative_cap: bool,
) -> tuple[str, float, float, tuple[str, ...]]:
    ratio = current / reference if reference > 0 else 1.0
    delta = current - reference
    ratio_7d = (
        current / reference_7d
        if reference_7d is not None and reference_7d > 0
        else None
    )
    sustained_ratio = (
        reference_7d / reference_30d
        if reference_7d is not None
        and reference_30d is not None
        and reference_30d > 0
        else None
    )

    if conservative_cap:
        # Temperature-sensitive or cycle-ambiguous telemetry is still useful for
        # relative condition, but it cannot justify an aggressive replacement
        # verdict without compensation/confirmation.
        if ratio >= VOLTAGE_DECLINING_RATIO and trend != "falling":
            state = "ok"
        elif ratio < VOLTAGE_WEAKENING_RATIO and trend == "falling":
            state = "weakening"
        else:
            state = "declining"
        return state, ratio, delta, ("aggressive_escalation_guarded",)

    if (
        ratio < VOLTAGE_REPLACE_RATIO
        and (
            trend == "falling"
            or (sustained_ratio is not None and sustained_ratio < 0.92)
        )
        and (ratio_7d is None or ratio_7d < 0.95)
    ):
        return "replace", ratio, delta, ("relative_voltage_deep_persistent_drop",)
    if ratio < VOLTAGE_WEAKENING_RATIO or (
        ratio < 0.96 and trend == "falling"
    ):
        return "weakening", ratio, delta, ("relative_voltage_material_drop",)
    if ratio < VOLTAGE_DECLINING_RATIO or trend == "falling":
        return "declining", ratio, delta, ("relative_voltage_decline",)
    return "ok", ratio, delta, ("relative_voltage_stable",)


def _battery_condition(
    *,
    current: float,
    reference: float,
    reference_7d: float | None,
    trend: str,
    behavior: str,
    conservative_cap: bool,
) -> tuple[str, float, float, tuple[str, ...]]:
    ratio = current / reference if reference > 0 else 1.0
    drop = max(0.0, reference - current)
    sustained_drop = (
        max(0.0, reference - reference_7d)
        if reference_7d is not None
        else 0.0
    )

    if (
        not conservative_cap
        and current <= BATTERY_REPLACE_PERCENT
        and reference_7d is not None
        and reference_7d <= BATTERY_REPLACE_7D_PERCENT
        and reference >= 30.0
        and drop >= 15.0
        and (trend == "falling" or sustained_drop >= 15.0)
    ):
        return "replace", ratio, current - reference, ("relative_battery_deep_persistent_drop",)

    if (
        current <= BATTERY_LOW_PERCENT
        and drop >= 5.0
        and (trend == "falling" or behavior == "monotonic")
    ) or (
        drop >= BATTERY_WEAKENING_DROP_PP
        and current <= 50.0
        and (trend == "falling" or sustained_drop >= 15.0)
    ):
        state = "declining" if conservative_cap else "weakening"
        return state, ratio, current - reference, ("relative_battery_material_drop",)

    if (
        drop >= BATTERY_STRONG_DECLINE_DROP_PP
        or (
            drop >= BATTERY_DECLINING_DROP_PP
            and (trend == "falling" or behavior == "monotonic")
        )
    ):
        return "declining", ratio, current - reference, ("relative_battery_decline",)

    return "ok", ratio, current - reference, ("relative_battery_stable",)


def assess_relative_health_v2(
    battery_current: BatteryHistorySummary | None,
    voltage_current: VoltageHistorySummary | None,
    battery_daily: Mapping[str, BatteryHistorySummary],
    voltage_daily: Mapping[str, VoltageHistorySummary],
    evidence_model: EvidenceModel,
    cycle_integrity: CycleIntegrity,
    baseline_v2: BaselineV2Assessment,
    *,
    persisted_baseline_mv: float | None,
    persisted_baseline_confidence: float | None,
) -> RelativeHealthAssessment:
    """Assess condition from the best available signal relative to own history.

    Signal selection is topology-aware.  A continuous voltage channel is preferred
    when informative.  If voltage is absent/static/quantized or a required
    temperature context makes an independent battery channel safer, battery
    percentage uses the same relative-history concept instead.
    """
    battery_level = (
        _finite(battery_current.p90_percent)
        if battery_current is not None and battery_current.issue is None
        else None
    )
    voltage_level = (
        _finite(voltage_current.p90_mv)
        if voltage_current is not None and voltage_current.issue is None
        else None
    )
    battery_coverage = (
        battery_current.coverage_ratio
        if battery_current is not None and battery_current.issue is None
        else 0.0
    )
    voltage_coverage = (
        voltage_current.coverage_ratio
        if voltage_current is not None and voltage_current.issue is None
        else 0.0
    )

    battery_series = _daily_battery_values(battery_daily)
    voltage_series = _daily_voltage_values(voltage_daily)
    battery_values = [value for _day, value in battery_series]
    voltage_values = [value for _day, value in voltage_series]
    battery_ref_30 = _upper_reference(battery_values)
    battery_ref_7 = _window_reference(battery_values, 7)
    voltage_ref_30 = _upper_reference(voltage_values)
    voltage_ref_7 = _window_reference(voltage_values, 7)

    behavior = evidence_model.battery_processing
    # The profiler's semantic behavior is not part of EvidenceModel, but the
    # processing contract contains the important volatile/upper-envelope split.
    battery_behavior = "volatile" if behavior == "upper_envelope" else (
        "monotonic" if behavior == "trend" else "static"
    )

    cycle_limited = (
        cycle_integrity.state != "stable"
        or baseline_v2.cycle_segment.state
        in {"possible_boundary", "current_boundary", "insufficient"}
        or (
            baseline_v2.cycle_segment.state == "segmented"
            and baseline_v2.eligibility != "eligible"
        )
    )
    freshness_limited = evidence_model.freshness_gate != "open"
    readiness_limited = evidence_model.decision_readiness != "ready"
    forced_limited = cycle_limited or freshness_limited or readiness_limited

    continuous_voltage = (
        voltage_level is not None
        and voltage_current is not None
        and voltage_coverage > 0
        and evidence_model.voltage_information.information == "continuous"
        and evidence_model.voltage_role in {"primary", "shared"}
    )
    battery_usable = battery_level is not None and battery_current is not None

    temperature_required = evidence_model.temperature_context == "required"
    shared_signal = evidence_model.battery_role == "shared"

    use_voltage = continuous_voltage
    assessment_mode = "relative_voltage"
    conservative_cap = False

    if temperature_required:
        if battery_usable and not shared_signal:
            use_voltage = False
            assessment_mode = "relative_battery_temperature_guard"
            forced_limited = True
        elif continuous_voltage:
            use_voltage = True
            assessment_mode = "temperature_guarded_relative_voltage"
            conservative_cap = True
            forced_limited = True

    if cycle_limited:
        conservative_cap = True
        forced_limited = True

    if use_voltage and voltage_level is not None:
        trend, recent_change = _trend(
            voltage_values,
            signal="voltage_mv",
            long_term_behavior=("volatile" if battery_behavior == "volatile" else None),
        )
        reference_days = len(voltage_values)
        reference_30 = voltage_ref_30
        reference_7 = voltage_ref_7

        persisted_usable = (
            persisted_baseline_mv is not None
            and persisted_baseline_mv > 0
            and persisted_baseline_confidence is not None
            and baseline_v2.eligibility == "eligible"
            and not cycle_limited
            and not temperature_required
        )
        if persisted_usable:
            reference = float(persisted_baseline_mv)
            assessment_mode = "persisted_voltage"
            confidence_cap = min(0.85, float(persisted_baseline_confidence))
        elif (temperature_required or cycle_limited) and reference_7 is not None:
            reference = reference_7
            confidence_cap = 0.50
        else:
            reference = reference_30 or reference_7 or voltage_level
            confidence_cap = 0.72

        calc_state, confidence = _calculation_quality(
            coverage=voltage_coverage,
            reference_days=reference_days,
            confidence_cap=confidence_cap,
            forced_limited=forced_limited,
        )
        state, ratio, delta, reasons = _voltage_condition(
            current=voltage_level,
            reference=reference,
            reference_7d=reference_7,
            reference_30d=reference_30,
            trend=trend,
            conservative_cap=conservative_cap,
        )
        limitations: list[str] = []
        if temperature_required:
            limitations.append("temperature_sensitive_signal_not_normalized")
        if cycle_limited:
            limitations.append("cycle_context_limits_escalation")
        if freshness_limited:
            limitations.append("freshness_not_open")
        if readiness_limited:
            limitations.append("evidence_not_fully_ready")
        return RelativeHealthAssessment(
            condition_state=state,
            calculation_state=calc_state,
            trend_state=trend,
            confidence=confidence,
            assessment_mode=assessment_mode,
            signal="voltage_mv",
            current_value=voltage_level,
            reference_7d=reference_7,
            reference_30d=reference_30,
            reference_used=reference,
            ratio_to_reference=ratio,
            delta_from_reference=delta,
            recent_vs_previous=recent_change,
            reasons=reasons,
            limitations=tuple(limitations),
        )

    if battery_usable and battery_level is not None:
        trend, recent_change = _trend(
            battery_values,
            signal="battery_percent",
            long_term_behavior=battery_behavior,
        )
        reference_days = len(battery_values)
        reference_30 = battery_ref_30
        reference_7 = battery_ref_7
        if cycle_limited and reference_7 is not None:
            reference = reference_7
        else:
            reference = reference_30 or reference_7 or battery_level
        confidence_cap = 0.60
        if assessment_mode == "relative_battery":
            assessment_mode = "relative_battery"
        calc_state, confidence = _calculation_quality(
            coverage=battery_coverage,
            reference_days=reference_days,
            confidence_cap=confidence_cap,
            forced_limited=forced_limited,
        )
        state, ratio, delta, reasons = _battery_condition(
            current=battery_level,
            reference=reference,
            reference_7d=reference_7,
            trend=trend,
            behavior=battery_behavior,
            conservative_cap=conservative_cap,
        )
        limitations: list[str] = []
        if evidence_model.voltage_information.information in {"static", "quantized"}:
            limitations.append("voltage_not_primary_condition_signal")
        if temperature_required:
            limitations.append("temperature_sensitive_voltage_bypassed")
        if cycle_limited:
            limitations.append("cycle_context_limits_escalation")
        if freshness_limited:
            limitations.append("freshness_not_open")
        if readiness_limited:
            limitations.append("evidence_not_fully_ready")
        return RelativeHealthAssessment(
            condition_state=state,
            calculation_state=calc_state,
            trend_state=trend,
            confidence=confidence,
            assessment_mode=assessment_mode,
            signal="battery_percent",
            current_value=battery_level,
            reference_7d=reference_7,
            reference_30d=reference_30,
            reference_used=reference,
            ratio_to_reference=ratio,
            delta_from_reference=delta,
            recent_vs_previous=recent_change,
            reasons=reasons,
            limitations=tuple(limitations),
        )

    return RelativeHealthAssessment(
        condition_state=None,
        calculation_state="unavailable",
        trend_state="insufficient",
        confidence=0.0,
        assessment_mode="unavailable",
        signal=None,
        current_value=None,
        reference_7d=None,
        reference_30d=None,
        reference_used=None,
        ratio_to_reference=None,
        delta_from_reference=None,
        recent_vs_previous=None,
        reasons=("no_usable_current_condition_signal",),
        limitations=tuple(evidence_model.limitations),
    )
