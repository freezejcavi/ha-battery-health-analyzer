"""Pure source-discovery logic for Battery Health Analyzer."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .const import (
    ISSUE_AMBIGUOUS_BATTERY,
    ISSUE_AMBIGUOUS_BATTERY_LOW,
    ISSUE_AMBIGUOUS_LAST_SEEN,
    ISSUE_AMBIGUOUS_OUTAGE,
    ISSUE_AMBIGUOUS_VOLTAGE,
    ISSUE_MISSING_BATTERY_PERCENT,
    ISSUE_MISSING_VOLTAGE,
)
from .models import DiscoveredBatteryDevice, EntityDescriptor

ScoreFunction = Callable[[EntityDescriptor], int | None]


@dataclass(frozen=True, slots=True)
class _Selection:
    entity_id: str | None
    tied_entity_ids: tuple[str, ...] = ()


def _select_best(
    entities: Iterable[EntityDescriptor], scorer: ScoreFunction
) -> _Selection:
    scored: list[tuple[int, str]] = []
    for entity in entities:
        if entity.disabled:
            continue
        score = scorer(entity)
        if score is not None:
            scored.append((score, entity.entity_id))

    if not scored:
        return _Selection(None)

    scored.sort(key=lambda item: (-item[0], item[1]))
    top_score = scored[0][0]
    tied = tuple(entity_id for score, entity_id in scored if score == top_score)
    if len(tied) > 1:
        return _Selection(None, tied)
    return _Selection(scored[0][1])


def _battery_score(entity: EntityDescriptor) -> int | None:
    if entity.domain != "sensor":
        return None

    text = entity.searchable_name
    is_battery_class = entity.device_class == "battery"
    has_battery_name = text.endswith("_battery") or "_battery_" in text
    is_percent = entity.unit in {"%", "percent", "percentage"}

    if not is_battery_class and not (has_battery_name and is_percent):
        return None

    return (
        100 * is_battery_class
        + 50 * is_percent
        + 25 * text.endswith("_battery")
        + 10 * has_battery_name
    )


def _battery_low_score(entity: EntityDescriptor) -> int | None:
    if entity.domain != "binary_sensor":
        return None

    text = entity.searchable_name
    is_battery_class = entity.device_class == "battery"
    has_low_name = "battery_low" in text or "low_battery" in text
    if not is_battery_class and not has_low_name:
        return None

    return 100 * is_battery_class + 50 * has_low_name


def _voltage_score(entity: EntityDescriptor) -> int | None:
    if entity.domain != "sensor":
        return None

    text = entity.searchable_name
    is_voltage_class = entity.device_class == "voltage"
    has_voltage_unit = entity.unit in {"V", "mV", "v", "mv"}
    has_battery_voltage = "battery_voltage" in text
    ends_voltage = entity.entity_id.endswith("_voltage")

    if not has_battery_voltage and not (
        ends_voltage and (is_voltage_class or has_voltage_unit)
    ):
        return None

    score = (
        140 * has_battery_voltage
        + 40 * is_voltage_class
        + 25 * has_voltage_unit
        + 20 * ends_voltage
    )
    if any(token in text for token in ("mains_voltage", "input_voltage", "output_voltage")):
        score -= 100
    return score


def _last_seen_score(entity: EntityDescriptor) -> int | None:
    if entity.domain != "sensor":
        return None
    text = entity.searchable_name
    if entity.entity_id.endswith("_last_seen"):
        return 120
    if "last_seen" in text:
        return 80
    return None


def _outage_score(entity: EntityDescriptor) -> int | None:
    if entity.domain != "sensor":
        return None
    text = entity.searchable_name
    if entity.entity_id.endswith("_power_outage_count"):
        return 140
    if "power_outage_count" in text:
        return 100
    return None


def discover_battery_devices(
    entities: Iterable[EntityDescriptor],
    device_names: dict[str, str | None] | None = None,
) -> list[DiscoveredBatteryDevice]:
    """Discover battery devices and safely pair their supporting entities."""
    names = device_names or {}
    grouped: dict[str, list[EntityDescriptor]] = defaultdict(list)
    for entity in entities:
        if entity.device_id is None or entity.disabled:
            continue
        grouped[entity.device_id].append(entity)

    discovered: list[DiscoveredBatteryDevice] = []
    for device_id, device_entities in grouped.items():
        battery = _select_best(device_entities, _battery_score)
        battery_low = _select_best(device_entities, _battery_low_score)

        if (
            battery.entity_id is None
            and not battery.tied_entity_ids
            and battery_low.entity_id is None
            and not battery_low.tied_entity_ids
        ):
            continue

        voltage = _select_best(device_entities, _voltage_score)
        last_seen = _select_best(device_entities, _last_seen_score)
        outage = _select_best(device_entities, _outage_score)

        issues: list[str] = []
        if battery.tied_entity_ids:
            issues.append(ISSUE_AMBIGUOUS_BATTERY)
        if battery_low.tied_entity_ids:
            issues.append(ISSUE_AMBIGUOUS_BATTERY_LOW)
        if voltage.tied_entity_ids:
            issues.append(ISSUE_AMBIGUOUS_VOLTAGE)
        if last_seen.tied_entity_ids:
            issues.append(ISSUE_AMBIGUOUS_LAST_SEEN)
        if outage.tied_entity_ids:
            issues.append(ISSUE_AMBIGUOUS_OUTAGE)
        if battery.entity_id is None:
            issues.append(ISSUE_MISSING_BATTERY_PERCENT)
        if voltage.entity_id is None:
            issues.append(ISSUE_MISSING_VOLTAGE)

        discovered.append(
            DiscoveredBatteryDevice(
                device_id=device_id,
                device_name=names.get(device_id),
                battery_entity_id=battery.entity_id,
                battery_low_entity_id=battery_low.entity_id,
                voltage_entity_id=voltage.entity_id,
                last_seen_entity_id=last_seen.entity_id,
                outage_entity_id=outage.entity_id,
                issues=tuple(issues),
            )
        )

    return sorted(
        discovered,
        key=lambda device: ((device.device_name or "").lower(), device.device_id),
    )

