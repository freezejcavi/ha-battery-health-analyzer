"""Pure battery-cycle integrity helpers for Battery Health Analyzer."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from .evidence import VoltageInformation
from .models import BatteryHistorySummary, VoltageHistorySummary

REFERENCE_DAYS = 7
REFERENCE_MIN_VALID_DAYS = 3
BATTERY_UPSHIFT_PP = 20.0
BATTERY_UPSHIFT_MAX_RANGE_PP = 10.0
BATTERY_ONLY_UPSHIFT_PP = 40.0
BATTERY_ONLY_MAX_RANGE_PP = 5.0
VOLTAGE_UPSHIFT_P50_MV = 150.0
VOLTAGE_UPSHIFT_FLOOR_MV = 75.0
VOLTAGE_ONLY_UPSHIFT_P50_MV = 250.0
VOLTAGE_ONLY_UPSHIFT_FLOOR_MV = 150.0


@dataclass(frozen=True, slots=True)
class CycleIntegrity:
    """Describe whether recent telemetry can safely share one battery cycle."""

    state: str
    history_usable: bool
    battery_reference_percent: float | None
    battery_reference_days: int
    battery_upshift_pp: float | None
    battery_signal: str
    voltage_reference_mv: float | None
    voltage_reference_days: int
    voltage_upshift_p50_mv: float | None
    voltage_upshift_floor_mv: float | None
    voltage_signal: str
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return compact read-only diagnostics."""

        def rounded(value: float | None, digits: int = 1) -> float | None:
            return round(value, digits) if value is not None else None

        return {
            "state": self.state,
            "history_usable": self.history_usable,
            "battery": {
                "reference_7d_percent": rounded(self.battery_reference_percent),
                "reference_days": self.battery_reference_days,
                "upshift_pp": rounded(self.battery_upshift_pp),
                "signal": self.battery_signal,
            },
            "voltage": {
                "reference_7d_mv": (
                    round(self.voltage_reference_mv)
                    if self.voltage_reference_mv is not None
                    else None
                ),
                "reference_days": self.voltage_reference_days,
                "upshift_p50_mv": rounded(self.voltage_upshift_p50_mv),
                "upshift_floor_mv": rounded(self.voltage_upshift_floor_mv),
                "signal": self.voltage_signal,
            },
            "reasons": list(self.reasons),
        }


def _recent_reference(
    daily: Mapping[str, Any],
    attribute: str,
) -> tuple[float | None, int]:
    """Return the median of the last seven valid daily upper-envelope values."""
    values: list[float] = []
    for day in sorted(daily)[-REFERENCE_DAYS:]:
        summary = daily[day]
        if getattr(summary, "issue", None) is not None:
            continue
        value = getattr(summary, attribute, None)
        if value is not None:
            values.append(float(value))
    if not values:
        return None, 0
    return float(median(values)), len(values)


def assess_cycle_integrity(
    battery_daily: Mapping[str, Any],
    voltage_daily: Mapping[str, Any],
    battery_current: BatteryHistorySummary | None,
    voltage_current: VoltageHistorySummary | None,
    voltage_information: VoltageInformation,
    freshness_state: str | None,
    battery_voltage_topology: str,
) -> CycleIntegrity:
    """Detect a recent regime upshift that may indicate a new battery cycle.

    Battery percentage and voltage may represent the same physical signal. A
    joint upshift is therefore promoted to ``probable_boundary`` only when the
    Evidence Model has established that the channels are independent. Coupled,
    shared or unknown topology remains a quarantined ``possible_boundary``.

    This helper is intentionally conservative and read-only. It never increments
    a battery cycle or issues a health verdict.
    """
    battery_reference, battery_reference_days = _recent_reference(
        battery_daily,
        "p90_percent",
    )
    voltage_reference, voltage_reference_days = _recent_reference(
        voltage_daily,
        "p90_mv",
    )

    trusted_freshness = freshness_state in {"fresh", "late"}
    if not trusted_freshness:
        return CycleIntegrity(
            state="insufficient",
            history_usable=False,
            battery_reference_percent=battery_reference,
            battery_reference_days=battery_reference_days,
            battery_upshift_pp=None,
            battery_signal="untrusted",
            voltage_reference_mv=voltage_reference,
            voltage_reference_days=voltage_reference_days,
            voltage_upshift_p50_mv=None,
            voltage_upshift_floor_mv=None,
            voltage_signal="untrusted",
            reasons=("freshness_untrusted",),
        )

    battery_upshift = None
    battery_signal = "unavailable"
    battery_strong = False
    battery_only_strong = False
    if (
        battery_current is not None
        and battery_current.issue is None
        and battery_current.median_percent is not None
        and battery_reference is not None
        and battery_reference_days >= REFERENCE_MIN_VALID_DAYS
    ):
        battery_upshift = battery_current.median_percent - battery_reference
        current_range = battery_current.range_percent
        battery_strong = (
            current_range is not None
            and current_range <= BATTERY_UPSHIFT_MAX_RANGE_PP
            and battery_upshift >= BATTERY_UPSHIFT_PP
        )
        battery_only_strong = (
            current_range is not None
            and current_range <= BATTERY_ONLY_MAX_RANGE_PP
            and battery_upshift >= BATTERY_ONLY_UPSHIFT_PP
        )
        battery_signal = "persistent_upshift" if battery_strong else "normal"
    elif battery_reference_days < REFERENCE_MIN_VALID_DAYS:
        battery_signal = "insufficient_reference"

    voltage_upshift_p50 = None
    voltage_upshift_floor = None
    voltage_signal = "unavailable"
    voltage_strong = False
    voltage_only_strong = False
    voltage_informative = voltage_information.information in {
        "continuous",
        "quantized",
    }
    if (
        voltage_informative
        and voltage_current is not None
        and voltage_current.issue is None
        and voltage_current.median_mv is not None
        and voltage_current.p10_mv is not None
        and voltage_reference is not None
        and voltage_reference_days >= REFERENCE_MIN_VALID_DAYS
    ):
        voltage_upshift_p50 = voltage_current.median_mv - voltage_reference
        voltage_upshift_floor = voltage_current.p10_mv - voltage_reference
        voltage_strong = (
            voltage_upshift_p50 >= VOLTAGE_UPSHIFT_P50_MV
            and voltage_upshift_floor >= VOLTAGE_UPSHIFT_FLOOR_MV
        )
        voltage_only_strong = (
            voltage_upshift_p50 >= VOLTAGE_ONLY_UPSHIFT_P50_MV
            and voltage_upshift_floor >= VOLTAGE_ONLY_UPSHIFT_FLOOR_MV
        )
        voltage_signal = "persistent_upshift" if voltage_strong else "normal"
    elif voltage_information.information == "static":
        voltage_signal = "static"
    elif voltage_reference_days < REFERENCE_MIN_VALID_DAYS:
        voltage_signal = "insufficient_reference"
    elif not voltage_informative:
        voltage_signal = "low_information"

    battery_available = battery_signal not in {
        "unavailable",
        "insufficient_reference",
    }
    voltage_available = voltage_signal not in {
        "unavailable",
        "insufficient_reference",
        "static",
        "low_information",
    }

    if battery_strong and voltage_strong:
        if battery_voltage_topology == "independent":
            state = "probable_boundary"
            reasons = ("independent_joint_persistent_upshift",)
        else:
            state = "possible_boundary"
            reasons = ("joint_upshift_not_independent",)
        return CycleIntegrity(
            state=state,
            history_usable=False,
            battery_reference_percent=battery_reference,
            battery_reference_days=battery_reference_days,
            battery_upshift_pp=battery_upshift,
            battery_signal=battery_signal,
            voltage_reference_mv=voltage_reference,
            voltage_reference_days=voltage_reference_days,
            voltage_upshift_p50_mv=voltage_upshift_p50,
            voltage_upshift_floor_mv=voltage_upshift_floor,
            voltage_signal=voltage_signal,
            reasons=reasons,
        )

    if battery_only_strong and not voltage_available:
        state = "possible_boundary"
        usable = False
        reasons = ("battery_only_large_upshift",)
    elif voltage_only_strong and not battery_available:
        state = "possible_boundary"
        usable = False
        reasons = ("voltage_only_large_upshift",)
    elif battery_available or voltage_available:
        state = "stable"
        usable = True
        reasons = ()
    else:
        state = "insufficient"
        usable = False
        reasons = ("recent_reference_insufficient",)

    return CycleIntegrity(
        state=state,
        history_usable=usable,
        battery_reference_percent=battery_reference,
        battery_reference_days=battery_reference_days,
        battery_upshift_pp=battery_upshift,
        battery_signal=battery_signal,
        voltage_reference_mv=voltage_reference,
        voltage_reference_days=voltage_reference_days,
        voltage_upshift_p50_mv=voltage_upshift_p50,
        voltage_upshift_floor_mv=voltage_upshift_floor,
        voltage_signal=voltage_signal,
        reasons=reasons,
    )
