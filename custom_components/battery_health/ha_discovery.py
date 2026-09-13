"""Home Assistant registry adapter for the pure discovery engine."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .discovery import discover_battery_devices
from .models import DiscoveredBatteryDevice, EntityDescriptor


def async_discover_battery_devices(
    hass: HomeAssistant,
) -> list[DiscoveredBatteryDevice]:
    """Build a read-only battery discovery snapshot from HA registries and states."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    descriptors: list[EntityDescriptor] = []
    device_names: dict[str, str | None] = {}

    for registry_entry in entity_registry.entities.values():
        if registry_entry.device_id is None:
            continue

        device_entry = device_registry.async_get(registry_entry.device_id)
        device_disabled = bool(
            device_entry is not None
            and getattr(device_entry, "disabled_by", None) is not None
        )
        if device_entry is not None:
            device_names[registry_entry.device_id] = (
                device_entry.name_by_user or device_entry.name
            )

        state = hass.states.get(registry_entry.entity_id)
        attributes = state.attributes if state is not None else {}
        device_class = attributes.get("device_class") or getattr(
            registry_entry, "original_device_class", None
        )

        descriptors.append(
            EntityDescriptor(
                entity_id=registry_entry.entity_id,
                device_id=registry_entry.device_id,
                domain=registry_entry.entity_id.partition(".")[0],
                state=state.state if state is not None else None,
                device_class=str(device_class) if device_class else None,
                unit=attributes.get("unit_of_measurement"),
                name=registry_entry.name,
                original_name=registry_entry.original_name,
                disabled=registry_entry.disabled_by is not None or device_disabled,
            )
        )

    return discover_battery_devices(descriptors, device_names)

