"""Recorder adapter for last-seen freshness and outage evidence."""

from __future__ import annotations

from datetime import datetime
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


async def _async_get_states(
    hass: HomeAssistant,
    entity_ids: list[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, list[State]]:
    """Fetch one Recorder batch for operability sources."""
    if not entity_ids:
        return {}

    query = partial(
        history.get_significant_states,
        hass,
        window_start,
        window_end,
        entity_ids,
        include_start_time_state=True,
        significant_changes_only=True,
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


async def async_get_operability_history(
    hass: HomeAssistant,
    last_seen_entity_ids: list[str],
    outage_entity_ids: list[str],
    window_end: datetime,
) -> OperabilitySnapshot:
    """Build adaptive freshness and reset-aware outage evidence from 24h history."""
    entity_ids = sorted(set(last_seen_entity_ids + outage_entity_ids))
    if not entity_ids:
        return OperabilitySnapshot({}, {})

    window_start = window_end - HISTORY_WINDOW
    states_by_entity = await _async_get_states(
        hass,
        entity_ids,
        window_start,
        window_end,
    )

    freshness = {}
    for entity_id in last_seen_entity_ids:
        states = states_by_entity.get(entity_id, [])
        report_timestamps = [
            parsed
            for state in states
            if (parsed := parse_last_seen(state.state)) is not None
        ]

        current_state = hass.states.get(entity_id)
        if current_state is not None:
            current_last_seen = parse_last_seen(current_state.state)
            source = "current" if current_last_seen is not None else "unavailable"
        else:
            current_last_seen = (
                parse_last_seen(states[-1].state) if states else None
            )
            source = "recorder" if current_last_seen is not None else "unavailable"

        freshness[entity_id] = summarize_freshness(
            report_timestamps,
            current_last_seen,
            window_end,
            window_start,
            source=source,
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
        outages[entity_id] = summarize_outage_history(
            points,
            window_start,
            window_end,
            latest_count=latest_count,
        )

    return OperabilitySnapshot(freshness=freshness, outages=outages)
