"""Pure freshness and power-outage evidence helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import floor, isfinite
from typing import Any

FRESHNESS_MIN_CADENCE_SAMPLES = 3
FRESHNESS_FRESH_RATIO = 1.5
FRESHNESS_LATE_RATIO = 3.0

ISSUE_INVALID_LAST_SEEN = "invalid_last_seen"
ISSUE_INSUFFICIENT_CADENCE = "insufficient_cadence"
ISSUE_FUTURE_LAST_SEEN = "future_last_seen"
ISSUE_NO_OUTAGE_HISTORY = "no_outage_history"
ISSUE_PARTIAL_OUTAGE_WINDOW = "partial_outage_window"
ISSUE_INVALID_OUTAGE_COUNTER = "invalid_outage_counter"


@dataclass(frozen=True, slots=True)
class FreshnessEvidence:
    """Device-specific report cadence and current freshness evidence."""

    supported: bool
    last_seen: datetime | None
    source: str
    age_seconds: float | None
    reports_24h: int
    cadence_samples: int
    median_gap_seconds: float | None
    p90_gap_seconds: float | None
    age_to_p90_ratio: float | None
    state: str
    issue: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics for the HA state attribute."""
        return {
            "supported": self.supported,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "source": self.source,
            "age_minutes": (
                round(self.age_seconds / 60, 1)
                if self.age_seconds is not None
                else None
            ),
            "reports_24h": self.reports_24h,
            "cadence_samples": self.cadence_samples,
            "median_gap_minutes": (
                round(self.median_gap_seconds / 60, 1)
                if self.median_gap_seconds is not None
                else None
            ),
            "p90_gap_minutes": (
                round(self.p90_gap_seconds / 60, 1)
                if self.p90_gap_seconds is not None
                else None
            ),
            "age_to_p90_ratio": (
                round(self.age_to_p90_ratio, 2)
                if self.age_to_p90_ratio is not None
                else None
            ),
            "state": self.state,
            "issue": self.issue,
        }


@dataclass(frozen=True, slots=True)
class OutageHistoryPoint:
    """One power-outage counter state transition."""

    timestamp: datetime
    count: int | None


@dataclass(frozen=True, slots=True)
class OutageEvidence:
    """Reset-aware 24-hour power-outage counter evidence."""

    supported: bool
    latest_count: int | None
    events_24h: int | None
    increment_transitions_24h: int
    resets_24h: int
    history_rows: int
    valid_rows: int
    issue: str | None = None
    max_positive_delta_24h: int | None = None
    max_positive_delta_at: datetime | None = None
    max_positive_delta_7d: int | None = None
    max_positive_delta_7d_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics for the HA state attribute."""
        return {
            "supported": self.supported,
            "latest_count": self.latest_count,
            "events_24h": self.events_24h,
            "increment_transitions_24h": self.increment_transitions_24h,
            "resets_24h": self.resets_24h,
            "history_rows": self.history_rows,
            "valid_rows": self.valid_rows,
            "max_positive_delta_24h": self.max_positive_delta_24h,
            "max_positive_delta_at": (
                self.max_positive_delta_at.isoformat()
                if self.max_positive_delta_at is not None
                else None
            ),
            "max_positive_delta_7d": self.max_positive_delta_7d,
            "max_positive_delta_7d_at": (
                self.max_positive_delta_7d_at.isoformat()
                if self.max_positive_delta_7d_at is not None
                else None
            ),
            "issue": self.issue,
        }


@dataclass(frozen=True, slots=True)
class OperabilitySnapshot:
    """Freshness and outage evidence keyed by source entity ID."""

    freshness: dict[str, FreshnessEvidence]
    outages: dict[str, OutageEvidence]


def _as_utc(value: datetime) -> datetime:
    """Return a UTC-aware datetime without relying on host local timezone."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_last_seen(value: str | int | float | datetime | None) -> datetime | None:
    """Parse Zigbee2MQTT/HA timestamp values into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)

    text = str(value).strip()
    if not text or text.casefold() in {"unknown", "unavailable", "none", "null"}:
        return None

    try:
        numeric = float(text)
    except ValueError:
        numeric = None

    if numeric is not None and isfinite(numeric):
        seconds = numeric / 1000 if abs(numeric) >= 1_000_000_000_000 else numeric
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return _as_utc(parsed)


def parse_outage_count(value: str | int | float | None) -> int | None:
    """Return a non-negative integer outage counter."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        return None
    return int(numeric)


def _percentile(values: list[float], quantile: float) -> float:
    """Return a linearly interpolated percentile."""
    if not values:
        raise ValueError("values must not be empty")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be in 0..1")

    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower_index = floor(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return ordered[lower_index] + (
        (ordered[upper_index] - ordered[lower_index]) * fraction
    )


def summarize_freshness(
    report_timestamps: Iterable[datetime],
    current_last_seen: datetime | None,
    observed_at: datetime,
    window_start: datetime,
    *,
    supported: bool = True,
    source: str = "current",
) -> FreshnessEvidence:
    """Summarize learned cadence while keeping report count bounded to 24h."""
    if not supported:
        return FreshnessEvidence(
            supported=False,
            last_seen=None,
            source="unavailable",
            age_seconds=None,
            reports_24h=0,
            cadence_samples=0,
            median_gap_seconds=None,
            p90_gap_seconds=None,
            age_to_p90_ratio=None,
            state="unavailable",
        )

    observed_utc = _as_utc(observed_at)
    window_start_utc = _as_utc(window_start)
    normalized = {
        _as_utc(timestamp)
        for timestamp in report_timestamps
        if _as_utc(timestamp) <= observed_utc
    }
    if current_last_seen is not None:
        normalized.add(_as_utc(current_last_seen))

    cadence_reports = sorted(normalized)
    reports_24h = [
        timestamp
        for timestamp in cadence_reports
        if window_start_utc <= timestamp <= observed_utc
    ]
    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(cadence_reports, cadence_reports[1:], strict=False)
        if later > earlier
    ]
    median_gap = _percentile(gaps, 0.5) if gaps else None
    p90_gap = _percentile(gaps, 0.9) if gaps else None

    if current_last_seen is None:
        return FreshnessEvidence(
            supported=True,
            last_seen=None,
            source=source,
            age_seconds=None,
            reports_24h=len(reports_24h),
            cadence_samples=len(gaps),
            median_gap_seconds=median_gap,
            p90_gap_seconds=p90_gap,
            age_to_p90_ratio=None,
            state="unavailable",
            issue=ISSUE_INVALID_LAST_SEEN,
        )

    current_utc = _as_utc(current_last_seen)
    age_seconds = (observed_utc - current_utc).total_seconds()
    if age_seconds < -300:
        return FreshnessEvidence(
            supported=True,
            last_seen=current_utc,
            source=source,
            age_seconds=age_seconds,
            reports_24h=len(reports_24h),
            cadence_samples=len(gaps),
            median_gap_seconds=median_gap,
            p90_gap_seconds=p90_gap,
            age_to_p90_ratio=None,
            state="invalid",
            issue=ISSUE_FUTURE_LAST_SEEN,
        )
    age_seconds = max(0.0, age_seconds)

    if len(gaps) < FRESHNESS_MIN_CADENCE_SAMPLES or p90_gap is None or p90_gap <= 0:
        return FreshnessEvidence(
            supported=True,
            last_seen=current_utc,
            source=source,
            age_seconds=age_seconds,
            reports_24h=len(reports_24h),
            cadence_samples=len(gaps),
            median_gap_seconds=median_gap,
            p90_gap_seconds=p90_gap,
            age_to_p90_ratio=None,
            state="insufficient",
            issue=ISSUE_INSUFFICIENT_CADENCE,
        )

    ratio = age_seconds / p90_gap
    if ratio <= FRESHNESS_FRESH_RATIO:
        state = "fresh"
    elif ratio <= FRESHNESS_LATE_RATIO:
        state = "late"
    else:
        state = "stale"

    return FreshnessEvidence(
        supported=True,
        last_seen=current_utc,
        source=source,
        age_seconds=age_seconds,
        reports_24h=len(reports_24h),
        cadence_samples=len(gaps),
        median_gap_seconds=median_gap,
        p90_gap_seconds=p90_gap,
        age_to_p90_ratio=ratio,
        state=state,
    )


def summarize_outage_history(
    points: Iterable[OutageHistoryPoint],
    window_start: datetime,
    window_end: datetime,
    *,
    supported: bool = True,
    latest_count: int | None = None,
) -> OutageEvidence:
    """Count reset-aware positive outage-counter deltas inside the window."""
    if not supported:
        return OutageEvidence(
            supported=False,
            latest_count=None,
            events_24h=None,
            increment_transitions_24h=0,
            resets_24h=0,
            history_rows=0,
            valid_rows=0,
        )
    if window_end <= window_start:
        raise ValueError("window_end must be after window_start")

    deduplicated: list[OutageHistoryPoint] = []
    for point in sorted(points, key=lambda item: item.timestamp):
        if deduplicated and point.timestamp == deduplicated[-1].timestamp:
            deduplicated[-1] = point
        else:
            deduplicated.append(point)

    valid = [point for point in deduplicated if point.count is not None]
    current_count = latest_count
    if current_count is None and valid:
        current_count = valid[-1].count

    if not deduplicated:
        return OutageEvidence(
            supported=True,
            latest_count=current_count,
            events_24h=None,
            increment_transitions_24h=0,
            resets_24h=0,
            history_rows=0,
            valid_rows=0,
            issue=ISSUE_NO_OUTAGE_HISTORY,
        )

    carry = next(
        (point for point in reversed(valid) if point.timestamp <= window_start),
        None,
    )
    relevant = [
        point for point in valid if window_start < point.timestamp <= window_end
    ]

    previous = carry
    events = 0
    increment_transitions = 0
    resets = 0
    max_positive_delta: int | None = None
    max_positive_delta_at: datetime | None = None
    for point in relevant:
        if (
            previous is not None
            and previous.count is not None
            and point.count is not None
        ):
            delta = point.count - previous.count
            if delta > 0:
                events += delta
                increment_transitions += 1
                if max_positive_delta is None or delta > max_positive_delta:
                    max_positive_delta = delta
                    max_positive_delta_at = point.timestamp
            elif delta < 0:
                resets += 1
        previous = point

    issue = None if carry is not None else ISSUE_PARTIAL_OUTAGE_WINDOW
    if not valid:
        issue = ISSUE_INVALID_OUTAGE_COUNTER

    return OutageEvidence(
        supported=True,
        latest_count=current_count,
        events_24h=events if valid else None,
        increment_transitions_24h=increment_transitions,
        resets_24h=resets,
        history_rows=len(deduplicated),
        valid_rows=len(valid),
        issue=issue,
        max_positive_delta_24h=max_positive_delta,
        max_positive_delta_at=max_positive_delta_at,
    )
