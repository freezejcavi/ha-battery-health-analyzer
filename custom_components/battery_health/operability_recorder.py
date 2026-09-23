"""Recorder adapter for freshness and outage evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant, State

from .const import HISTORY_WINDOW
from .operability import (
    OperabilitySnapshot,
    OutageHistoryPoint,
    parse_last_seen,
    parse_outage_count,
    summarize_freshness,
    summarize_outage_history,
)

CADENCE_WINDOW = timedelta(days=7)
OUTAGE_INCIDENT_WINDOW = timedelta(days=7)


async def _async_get_states(
    hass: HomeAssistant,
    entity_ids: list[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, list[State]]:
    """Fetch one Recorder batch for outage-counter sources."""
    if not entity_ids:
        return {}

    query = partial(
        history.get_significant_states,
        hass,
        window_start,
        window_end,
        entity_ids,
        include_start_time_state=True,
        significant_changes_only=False,
        minimal_response=False,
        no_attributes=True,
    )
    states_by_entity = await get_instance(hass).async_add_executor_job(query)
    return {
        entity_id: [
            state
            for state in states_by_entity.get(entity_id, [])
            if isinstance(state, State)
        ]
        for entity_id in entity_ids
    }


def _select_last_seen(
    current_state: State | None,
    cadence_timestamps: tuple[datetime, ...],
) -> tuple[datetime | None, str]:
    """Prefer a valid live timestamp, otherwise use the learned cadence Store."""
    if current_state is not None:
        current_last_seen = parse_last_seen(current_state.state)
        if current_last_seen is not None:
            return current_last_seen, "current"

    if cadence_timestamps:
        return cadence_timestamps[-1], "cadence_store"

    return None, "unavailable"


def _reports_24h(
    learned: tuple[datetime, ...],
    current_last_seen: datetime | None,
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Count distinct learned/live reports inside the current 24-hour window."""
    recent = {
        timestamp for timestamp in learned if window_start <= timestamp <= window_end
    }
    if (
        current_last_seen is not None
        and window_start <= current_last_seen <= window_end
    ):
        recent.add(current_last_seen)
    return len(recent)


async def async_get_operability_history(
    hass: HomeAssistant,
    last_seen_entity_ids: list[str],
    outage_entity_ids: list[str],
    window_end: datetime,
    *,
    cadence_timestamps: dict[str, tuple[datetime, ...]] | None = None,
) -> OperabilitySnapshot:
    """Build live-learned freshness plus reset-aware 24h outage evidence."""
    cadence_timestamps = cadence_timestamps or {}
    window_start = window_end - HISTORY_WINDOW
    cadence_window_start = window_end - CADENCE_WINDOW
    outage_incident_start = window_end - OUTAGE_INCIDENT_WINDOW

    # last_seen cadence is intentionally not learned from Recorder. Real HA
    # validation showed timestamp entities yielding only a start/current row.
    # Recorder remains useful for outage counters, whose state transitions are
    # persisted reliably in the same environment.
    states_by_entity = await _async_get_states(
        hass,
        outage_entity_ids,
        outage_incident_start,
        window_end,
    )

    freshness = {}
    for entity_id in last_seen_entity_ids:
        learned = cadence_timestamps.get(entity_id, ())
        current_last_seen, source = _select_last_seen(
            hass.states.get(entity_id),
            learned,
        )
        evidence = summarize_freshness(
            learned,
            current_last_seen,
            window_end,
            cadence_window_start,
            source=source,
        )
        freshness[entity_id] = replace(
            evidence,
            reports_24h=_reports_24h(
                learned,
                current_last_seen,
                window_start,
                window_end,
            ),
        )

    outages = {}
    for entity_id in outage_entity_ids:
        states = states_by_entity.get(entity_id, [])
        current_state = hass.states.get(entity_id)
        latest_count = (
            parse_outage_count(current_state.state)
            if current_state is not None
            else None
        )
        points = [
            OutageHistoryPoint(
                timestamp=state.last_changed,
                count=parse_outage_count(state.state),
            )
            for state in states
        ]
        evidence = summarize_outage_history(
            points,
            window_start,
            window_end,
            latest_count=latest_count,
        )
        incident_evidence = summarize_outage_history(
            points,
            outage_incident_start,
            window_end,
            latest_count=latest_count,
        )
        evidence = replace(
            evidence,
            max_positive_delta_7d=incident_evidence.max_positive_delta_24h,
            max_positive_delta_7d_at=incident_evidence.max_positive_delta_at,
        )
        if current_state is not None and latest_count is None:
            evidence = replace(evidence, latest_count=None)
        outages[entity_id] = evidence

    return OperabilitySnapshot(freshness=freshness, outages=outages)
