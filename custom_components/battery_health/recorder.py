"""Batch Recorder adapter for voltage history."""

from __future__ import annotations

from datetime import datetime
from functools import partial

from homeassistant.components.recorder import get_instance, history
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, State

from .const import HISTORY_WINDOW
from .models import VoltageHistoryPoint, VoltageHistorySummary
from .statistics import normalize_voltage_mv, summarize_voltage_history


async def async_get_voltage_history(
    hass: HomeAssistant,
    entity_ids: list[str],
    window_end: datetime,
) -> dict[str, VoltageHistorySummary]:
    """Fetch all voltage histories in one Recorder executor operation."""
    if not entity_ids:
        return {}

    window_start = window_end - HISTORY_WINDOW
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

    result: dict[str, VoltageHistorySummary] = {}
    for entity_id in entity_ids:
        current_state = hass.states.get(entity_id)
        unit = (
            current_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            if current_state is not None
            else None
        )
        unsupported_unit = normalize_voltage_mv(0, unit) is None
        points = [
            VoltageHistoryPoint(
                timestamp=state.last_changed,
                voltage_mv=normalize_voltage_mv(state.state, unit),
            )
            for state in states_by_entity.get(entity_id, [])
            if isinstance(state, State)
        ]
        result[entity_id] = summarize_voltage_history(
            points,
            window_start,
            window_end,
            unsupported_unit=unsupported_unit,
        )

    return result
