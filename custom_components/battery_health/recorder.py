"""Batch Recorder adapters for short- and long-term telemetry."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance, history
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, State

from .baseline import parse_battery_percent, select_battery_percent
from .const import HISTORY_WINDOW, LONG_TERM_WINDOW_DAYS
from .models import (
    BatteryHistoryPoint,
    BatteryHistorySummary,
    LongTermHistorySnapshot,
    NumericHistoryPoint,
    NumericHistorySummary,
    RecorderHistorySnapshot,
    VoltageHistoryPoint,
    VoltageHistorySummary,
)
from .statistics import (
    normalize_temperature_c,
    normalize_voltage_mv,
    summarize_battery_history,
    summarize_numeric_history,
    summarize_voltage_history,
)


async def _async_get_states(
    hass: HomeAssistant,
    entity_ids: list[str],
    window_start: datetime,
    window_end: datetime,
) -> dict[str, list[State]]:
    """Fetch one Recorder state batch."""
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
        no_attributes=False,
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


def _voltage_points(
    hass: HomeAssistant,
    entity_id: str,
    states: list[State],
) -> tuple[list[VoltageHistoryPoint], tuple[str, ...], bool]:
    """Normalize Recorder voltage states and preserve source-unit evidence."""
    current_state = hass.states.get(entity_id)
    current_unit = (
        current_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if current_state is not None
        else None
    )
    source_units: set[str] = set()
    supported_unit_found = False
    points: list[VoltageHistoryPoint] = []

    for state in states:
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

    return points, tuple(sorted(source_units)), supported_unit_found


def _battery_points(states: list[State]) -> list[BatteryHistoryPoint]:
    """Normalize Recorder battery states without skipping invalid gaps."""
    return [
        BatteryHistoryPoint(
            timestamp=state.last_changed,
            battery_percent=parse_battery_percent(state.state),
        )
        for state in states
    ]


def _temperature_points(
    hass: HomeAssistant,
    entity_id: str,
    states: list[State],
) -> list[NumericHistoryPoint]:
    """Normalize Recorder temperature states to degrees Celsius."""
    current_state = hass.states.get(entity_id)
    current_unit = (
        current_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if current_state is not None
        else None
    )
    return [
        NumericHistoryPoint(
            timestamp=state.last_changed,
            value=normalize_temperature_c(
                state.state,
                state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) or current_unit,
            ),
        )
        for state in states
    ]


async def async_get_recorder_history(
    hass: HomeAssistant,
    voltage_entity_ids: list[str],
    battery_entity_ids: list[str],
    window_end: datetime,
) -> RecorderHistorySnapshot:
    """Fetch 24h voltage and battery histories in one Recorder operation."""
    entity_ids = sorted(set(voltage_entity_ids + battery_entity_ids))
    if not entity_ids:
        return RecorderHistorySnapshot({}, {}, {}, {})

    window_start = window_end - HISTORY_WINDOW
    states_by_entity = await _async_get_states(
        hass,
        entity_ids,
        window_start,
        window_end,
    )

    voltage_history: dict[str, VoltageHistorySummary] = {}
    for entity_id in voltage_entity_ids:
        points, source_units, supported_unit_found = _voltage_points(
            hass,
            entity_id,
            states_by_entity.get(entity_id, []),
        )
        voltage_history[entity_id] = replace(
            summarize_voltage_history(
                points,
                window_start,
                window_end,
                unsupported_unit=not supported_unit_found,
            ),
            source_units=source_units,
        )

    battery_percent: dict[str, float | None] = {}
    battery_percent_source: dict[str, str] = {}
    battery_history: dict[str, BatteryHistorySummary] = {}
    for entity_id in battery_entity_ids:
        states = states_by_entity.get(entity_id, [])
        current_state = hass.states.get(entity_id)
        history_values = [state.state for state in states]
        value, source = select_battery_percent(
            current_state.state if current_state is not None else None,
            history_values,
        )
        battery_percent[entity_id] = value
        battery_percent_source[entity_id] = source
        battery_history[entity_id] = summarize_battery_history(
            _battery_points(states),
            window_start,
            window_end,
        )

    return RecorderHistorySnapshot(
        voltage_history,
        battery_percent,
        battery_percent_source,
        battery_history,
    )


def _complete_local_day_windows(
    hass: HomeAssistant,
    observed_at: datetime,
) -> list[tuple[str, datetime, datetime]]:
    """Return the previous 30 complete local calendar days as UTC windows."""
    timezone = ZoneInfo(hass.config.time_zone)
    local_now = observed_at.astimezone(timezone)
    end_date = local_now.date()
    start_date = end_date - timedelta(days=LONG_TERM_WINDOW_DAYS)

    windows: list[tuple[str, datetime, datetime]] = []
    for offset in range(LONG_TERM_WINDOW_DAYS):
        day = start_date + timedelta(days=offset)
        next_day = day + timedelta(days=1)
        local_start = datetime.combine(day, time.min, tzinfo=timezone)
        local_end = datetime.combine(next_day, time.min, tzinfo=timezone)
        windows.append(
            (
                day.isoformat(),
                local_start.astimezone(UTC),
                local_end.astimezone(UTC),
            )
        )
    return windows


async def async_get_long_term_history(
    hass: HomeAssistant,
    voltage_entity_ids: list[str],
    battery_entity_ids: list[str],
    temperature_entity_ids: list[str],
    observed_at: datetime,
) -> LongTermHistorySnapshot:
    """Fetch 30 complete local days once and convert them to daily aggregates."""
    windows = _complete_local_day_windows(hass, observed_at)
    if not windows:
        return LongTermHistorySnapshot({}, {}, {})

    query_start = windows[0][1]
    query_end = windows[-1][2]
    entity_ids = sorted(
        set(voltage_entity_ids + battery_entity_ids + temperature_entity_ids)
    )
    states_by_entity = await _async_get_states(
        hass,
        entity_ids,
        query_start,
        query_end,
    )

    battery_daily: dict[str, dict[str, BatteryHistorySummary]] = {}
    for entity_id in battery_entity_ids:
        points = _battery_points(states_by_entity.get(entity_id, []))
        battery_daily[entity_id] = {
            day: summarize_battery_history(points, start, end)
            for day, start, end in windows
        }

    voltage_daily: dict[str, dict[str, VoltageHistorySummary]] = {}
    for entity_id in voltage_entity_ids:
        points, source_units, supported_unit_found = _voltage_points(
            hass,
            entity_id,
            states_by_entity.get(entity_id, []),
        )
        voltage_daily[entity_id] = {
            day: replace(
                summarize_voltage_history(
                    points,
                    start,
                    end,
                    unsupported_unit=not supported_unit_found,
                ),
                source_units=source_units,
            )
            for day, start, end in windows
        }

    temperature_daily: dict[str, dict[str, NumericHistorySummary]] = {}
    for entity_id in temperature_entity_ids:
        points = _temperature_points(
            hass,
            entity_id,
            states_by_entity.get(entity_id, []),
        )
        temperature_daily[entity_id] = {
            day: summarize_numeric_history(points, start, end)
            for day, start, end in windows
        }

    return LongTermHistorySnapshot(
        battery_daily=battery_daily,
        voltage_daily=voltage_daily,
        temperature_daily=temperature_daily,
    )
