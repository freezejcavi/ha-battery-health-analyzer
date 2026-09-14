"""Batch Recorder adapter for voltage and battery history."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from functools import partial

from homeassistant.components.recorder import get_instance, history
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, State

from .baseline import select_battery_percent
from .const import HISTORY_WINDOW
from .models import (
    RecorderHistorySnapshot,
    VoltageHistoryPoint,
    VoltageHistorySummary,
)
from .statistics import normalize_voltage_mv, summarize_voltage_history


async def async_get_recorder_history(
    hass: HomeAssistant,
    voltage_entity_ids: list[str],
    battery_entity_ids: list[str],
    window_end: datetime,
) -> RecorderHistorySnapshot:
    """Fetch voltage and battery histories in one Recorder operation."""
    entity_ids = sorted(set(voltage_entity_ids + battery_entity_ids))
    if not entity_ids:
        return RecorderHistorySnapshot({}, {}, {})

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
        no_attributes=False,
    )
    states_by_entity = await get_instance(hass).async_add_executor_job(query)

    voltage_history: dict[str, VoltageHistorySummary] = {}
    for entity_id in voltage_entity_ids:
        current_state = hass.states.get(entity_id)
        current_unit = (
            current_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            if current_state is not None
            else None
        )
        source_units: set[str] = set()
        supported_unit_found = False
        points: list[VoltageHistoryPoint] = []
        for state in states_by_entity.get(entity_id, []):
            if not isinstance(state, State):
                continue

            state_unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            unit = state_unit or current_unit
            if unit:
                source_units.add(str(unit))
            if normalize_voltage_mv(0, unit) is not None:
                supported_unit_found = True
            points.append(
                VoltageHistoryPoint(
                    timestamp=state.last_changed,
                    voltage_mv=normalize_voltage_mv(state.state, unit),
                )
            )

        voltage_history[entity_id] = replace(
            summarize_voltage_history(
                points,
                window_start,
                window_end,
                unsupported_unit=not supported_unit_found,
            ),
            source_units=tuple(sorted(source_units)),
        )

    battery_percent: dict[str, float | None] = {}
    battery_percent_source: dict[str, str] = {}
    for entity_id in battery_entity_ids:
        current_state = hass.states.get(entity_id)
        history_values = [
            state.state
            for state in states_by_entity.get(entity_id, [])
            if isinstance(state, State)
        ]
        value, source = select_battery_percent(
            current_state.state if current_state is not None else None,
            history_values,
        )
        battery_percent[entity_id] = value
        battery_percent_source[entity_id] = source

    return RecorderHistorySnapshot(
        voltage_history,
        battery_percent,
        battery_percent_source,
    )
