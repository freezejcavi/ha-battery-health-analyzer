from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing replacement target: {label}")
    if text.count(old) != 1:
        raise RuntimeError(f"replacement target not unique: {label}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_i = text.find(start)
    if start_i < 0:
        raise RuntimeError(f"missing start marker: {label}")
    end_i = text.find(end, start_i)
    if end_i < 0:
        raise RuntimeError(f"missing end marker: {label}")
    return text[:start_i] + replacement + text[end_i:]


sensor_path = ROOT / "custom_components/battery_health/sensor.py"
sensor = sensor_path.read_text()

sensor = replace_once(
    sensor,
    "from __future__ import annotations\n\nfrom typing import Any\n",
    "from __future__ import annotations\n\nimport logging\nfrom typing import Any\n",
    "sensor logging import",
)
sensor = replace_once(
    sensor,
    "from homeassistant.core import HomeAssistant, callback\n",
    "from homeassistant.core import HomeAssistant, callback\nfrom homeassistant.helpers import entity_registry as er\n",
    "entity registry import",
)
sensor = replace_once(
    sensor,
    "from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo\n",
    "from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo\nfrom homeassistant.helpers.entity import EntityCategory\n",
    "entity category import",
)
sensor = replace_once(
    sensor,
    "from homeassistant.helpers.update_coordinator import CoordinatorEntity\n",
    "from homeassistant.helpers.update_coordinator import CoordinatorEntity\nfrom homeassistant.util import slugify\n",
    "slugify import",
)

legacy_import_pattern = re.compile(
    r"from \.health import \(\n(?:.*\n)*?\)\n"
    r"from \.health import \(\n(?:.*\n)*?\)\n"
    r"from \.health import \(\n(?:.*\n)*?\)\n",
)
sensor, count = legacy_import_pattern.subn("", sensor, count=1)
if count != 1:
    raise RuntimeError("legacy health imports not removed exactly once")

helper_marker = "\n\ndef _service_device_info(entry: ConfigEntry) -> DeviceInfo:\n"
helpers = '''

_LOGGER = logging.getLogger(__name__)

_PUBLIC_REASON = {
    "ok": "Stable relative to own history",
    "declining": "Early persistent decline from own history",
    "weakening": "Material persistent decline from own history",
    "replace": "Deep persistent decline from own history",
}

_LIMITATION_NOTES = {
    "temperature_sensitive_signal_not_normalized": "Temperature affects the voltage comparison",
    "temperature_sensitive_voltage_bypassed": "Voltage was bypassed because temperature affects it",
    "cycle_context_limits_escalation": "Battery-cycle context limits escalation",
    "freshness_not_open": "Freshness evidence is limited",
    "evidence_not_fully_ready": "Supporting evidence is limited",
    "low_current_voltage_coverage": "Current voltage history coverage is limited",
    "low_current_battery_coverage": "Current battery history coverage is limited",
}


def _canonical_health_entity_id(
    battery_entity_id: str | None,
    device_id: str,
) -> str:
    """Return a short entity ID derived only from the source battery entity."""
    object_id = ""
    if battery_entity_id and "." in battery_entity_id:
        object_id = battery_entity_id.split(".", 1)[1]
    if object_id.endswith("_battery") and len(object_id) > len("_battery"):
        object_id = object_id[: -len("_battery")]
    if not object_id:
        object_id = f"battery_{device_id[:8]}"
    return f"sensor.{object_id}_health"


def _looks_like_generated_entity_id(entity_id: str, legacy_full_name: str) -> bool:
    """Return whether an ID still looks system-generated from the old full name."""
    if "." not in entity_id:
        return False
    object_id = entity_id.split(".", 1)[1]
    legacy_slug = slugify(legacy_full_name)
    padded = f"_{object_id}_"
    token = f"_{legacy_slug}_"
    return (
        object_id == legacy_slug
        or object_id.startswith(f"{legacy_slug}_")
        or object_id.endswith(f"_{legacy_slug}")
        or token in padded
    )


def _rename_registry_entity_if_generated(
    registry: er.EntityRegistry,
    current_entity_id: str | None,
    target_entity_id: str,
    legacy_full_name: str,
) -> bool:
    """Rename only old generated IDs; preserve explicit user custom IDs."""
    if current_entity_id is None or current_entity_id == target_entity_id:
        return False
    if not _looks_like_generated_entity_id(current_entity_id, legacy_full_name):
        return False
    target_entry = registry.async_get(target_entity_id)
    if target_entry is not None:
        _LOGGER.warning(
            "Cannot migrate %s to %s because the target already exists",
            current_entity_id,
            target_entity_id,
        )
        return False
    registry.async_update_entity(current_entity_id, new_entity_id=target_entity_id)
    _LOGGER.info("Migrated entity ID %s -> %s", current_entity_id, target_entity_id)
    return True


def _migrate_health_entity_ids(hass: HomeAssistant, entry: ConfigEntry, devices) -> None:
    """Migrate dev26 generated health IDs to stable source-derived IDs."""
    registry = er.async_get(hass)
    for device in devices:
        unique_id = f"{entry.entry_id}_{device.device_id}_health"
        current_entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        target_entity_id = _canonical_health_entity_id(
            device.battery_entity_id,
            device.device_id,
        )
        display_name = device.device_name or device.device_id[:8]
        _rename_registry_entity_if_generated(
            registry,
            current_entity_id,
            target_entity_id,
            f"{display_name} Battery health",
        )

    summary_unique_id = f"{entry.entry_id}_health_summary"
    summary_current = registry.async_get_entity_id("sensor", DOMAIN, summary_unique_id)
    _rename_registry_entity_if_generated(
        registry,
        summary_current,
        "sensor.battery_health_summary",
        f"{NAME} Summary",
    )


def _public_health_attributes(assessment) -> dict[str, Any]:
    """Return the compact user-facing explanation for one health state."""
    if assessment is None or assessment.condition_state not in V2_CONDITION_STATES:
        return {
            "trend": "insufficient",
            "confidence_percent": 0,
            "calculation": "unavailable",
            "basis": "unavailable",
            "reason": "No usable battery telemetry",
        }

    is_voltage = assessment.signal == "voltage_mv"
    basis = "voltage" if is_voltage else "battery_percent"
    unit = "mV" if is_voltage else "%"
    current = assessment.current_value
    reference = assessment.reference_used
    if is_voltage:
        current_value = round(current) if current is not None else None
        reference_value = round(reference) if reference is not None else None
        change = (
            round((assessment.ratio_to_reference - 1.0) * 100.0, 1)
            if assessment.ratio_to_reference is not None
            else None
        )
        change_unit = "%"
    else:
        current_value = round(current, 1) if current is not None else None
        reference_value = round(reference, 1) if reference is not None else None
        change = (
            round(assessment.delta_from_reference, 1)
            if assessment.delta_from_reference is not None
            else None
        )
        change_unit = "pp"

    attributes: dict[str, Any] = {
        "trend": assessment.trend_state,
        "confidence_percent": round(assessment.confidence * 100),
        "calculation": assessment.calculation_state,
        "basis": basis,
        "current": current_value,
        "reference": reference_value,
        "unit": unit,
        "change_from_reference": change,
        "change_unit": change_unit,
        "reason": _PUBLIC_REASON[assessment.condition_state],
    }
    if assessment.calculation_state == "limited":
        notes = [
            _LIMITATION_NOTES[item]
            for item in assessment.limitations
            if item in _LIMITATION_NOTES
        ]
        if notes:
            attributes["note"] = "; ".join(dict.fromkeys(notes))
    return attributes
'''
sensor = replace_once(sensor, helper_marker, helpers + helper_marker, "public helpers")

sensor = replace_once(
    sensor,
    "    coordinator: BatteryHealthCoordinator = entry.runtime_data\n    known_device_ids = {device.device_id for device in coordinator.data.devices}\n",
    "    coordinator: BatteryHealthCoordinator = entry.runtime_data\n    _migrate_health_entity_ids(hass, entry, coordinator.data.devices)\n    known_device_ids = {device.device_id for device in coordinator.data.devices}\n",
    "entity id migration call",
)
sensor = sensor.replace(
    "                    device.device_id,\n                    device.device_name,\n",
    "                    device.device_id,\n                    device.device_name,\n                    device.battery_entity_id,\n",
)
if sensor.count("device.battery_entity_id,") < 2:
    raise RuntimeError("battery source was not passed to both entity constructors")
sensor = replace_once(
    sensor,
    "        device_id: str,\n        device_name: str | None,\n    ) -> None:\n",
    "        device_id: str,\n        device_name: str | None,\n        battery_entity_id: str | None,\n    ) -> None:\n",
    "device constructor source argument",
)
sensor = replace_once(
    sensor,
    "        self._attr_name = f\"{display_name} Battery health\"\n        self._attr_unique_id = f\"{entry.entry_id}_{device_id}_health\"\n",
    "        self._attr_name = f\"{display_name} Battery health\"\n        self.entity_id = _canonical_health_entity_id(battery_entity_id, device_id)\n        self._attr_unique_id = f\"{entry.entry_id}_{device_id}_health\"\n",
    "canonical device entity id",
)

method_start = sensor.index(
    "    @property\n    def extra_state_attributes(self) -> dict[str, Any]:",
    sensor.index("class BatteryHealthDeviceSensor"),
)
method_end = sensor.index("\n\n\nclass BatteryHealthSummarySensor", method_start)
new_method = '''    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose only user-facing variable evidence that explains the state."""
        assessment = self.coordinator.health_v2_assessments.get(self._device_id)
        return _public_health_attributes(assessment)
'''
sensor = sensor[:method_start] + new_method + sensor[method_end:]

sensor = replace_once(
    sensor,
    "        self._attr_unique_id = f\"{entry.entry_id}_health_summary\"\n        self._attr_device_info = _service_device_info(entry)\n",
    "        self.entity_id = \"sensor.battery_health_summary\"\n        self._attr_unique_id = f\"{entry.entry_id}_health_summary\"\n        self._attr_device_info = _service_device_info(entry)\n",
    "canonical summary id",
)

sensor = replace_once(
    sensor,
    "    _attr_has_entity_name = True\n    _attr_name = \"Discovered devices\"\n    _attr_icon = \"mdi:battery-search\"\n    _unrecorded_attributes = frozenset({MATCH_ALL})\n",
    "    _attr_has_entity_name = True\n    _attr_name = \"Discovered devices\"\n    _attr_icon = \"mdi:battery-search\"\n    _attr_entity_category = EntityCategory.DIAGNOSTIC\n    _attr_entity_registry_enabled_default = False\n    _unrecorded_attributes = frozenset({MATCH_ALL})\n",
    "diagnostic sensor visibility",
)

sensor = re.sub(
    r"\n        legacy_health_counts = \{state: 0 for state in LEGACY_HEALTH_STATES\}\n"
    r"        legacy_health_paths: dict\[str, int\] = \{\}",
    "",
    sensor,
    count=1,
)

legacy_device_start = sensor.index(
    "            health = self.coordinator.health_assessments.get(device.device_id)"
)
legacy_device_end = sensor.index(
    "            health_v2 = self.coordinator.health_v2_assessments.get(device.device_id)",
    legacy_device_start,
)
sensor = sensor[:legacy_device_start] + sensor[legacy_device_end:]
sensor = replace_once(
    sensor,
    "            diagnostics[\"health_v2_shadow\"] = (\n                health_v2.as_dict() if health_v2 is not None else None\n            )\n",
    "",
    "v2 shadow mirror",
)

legacy_summary_start = sensor.index(
    "        legacy_summary_state, _legacy_summary_counts = summarize_legacy_health_states("
)
legacy_summary_end = sensor.index(
    "        if health_v2_conditions[\"replace\"]:", legacy_summary_start
)
sensor = sensor[:legacy_summary_start] + sensor[legacy_summary_end:]

legacy_threshold_start = sensor.index("        thresholds = {\n")
legacy_threshold_end = sensor.index("        health_v2_thresholds = {\n", legacy_threshold_start)
sensor = sensor[:legacy_threshold_start] + sensor[legacy_threshold_end:]

for line in (
    '            "legacy_health_summary": legacy_summary_state,\n',
    '            "legacy_health_states": legacy_health_counts,\n',
    '            "legacy_health_paths": legacy_health_paths,\n',
    '            "legacy_health_thresholds": thresholds,\n',
):
    if line not in sensor:
        raise RuntimeError(f"missing return legacy line: {line.strip()}")
    sensor = sensor.replace(line, "", 1)

alias_start = sensor.index(
    "            # Transitional v2 aliases retained for diagnostic compatibility."
)
alias_end_marker = '            "shadow_health_thresholds": thresholds,\n'
alias_end = sensor.index(alias_end_marker, alias_start) + len(alias_end_marker)
sensor = sensor[:alias_start] + sensor[alias_end:]

sensor_path.write_text(sensor)

coordinator_path = ROOT / "custom_components/battery_health/coordinator.py"
coordinator = coordinator_path.read_text()
coordinator = replace_once(
    coordinator,
    "from .health import ShadowHealthAssessment, assess_shadow_health\n",
    "",
    "legacy health import",
)
coordinator = replace_once(
    coordinator,
    "        self.health_assessments: dict[str, ShadowHealthAssessment] = {}\n",
    "",
    "legacy health map",
)
coordinator = replace_once(
    coordinator,
    '        """Evaluate evidence, cycle, baseline and both health models for one device."""\n',
    '        """Evaluate evidence, cycle, baseline and the production health model."""\n',
    "decision docstring",
)
legacy_eval_start = coordinator.index(
    "        # Retain the dev23 classifier only as a temporary legacy diagnostic mirror."
)
legacy_eval_end = coordinator.index(
    "        # Health Model v2 is the production condition model.", legacy_eval_start
)
coordinator = coordinator[:legacy_eval_start] + coordinator[legacy_eval_end:]
coordinator = replace_once(
    coordinator,
    "        self.health_assessments = {}\n",
    "",
    "legacy health reset",
)
coordinator_path.write_text(coordinator)

manifest_path = ROOT / "custom_components/battery_health/manifest.json"
manifest = manifest_path.read_text()
manifest = replace_once(
    manifest,
    '  "version": "0.1.0-dev.26"',
    '  "version": "0.1.0-dev.27"',
    "manifest version",
)
manifest_path.write_text(manifest)

readme_path = ROOT / "README.md"
readme = readme_path.read_text()
readme = replace_once(
    readme,
    "**Current development version: `0.1.0-dev.26`**",
    "**Current development version: `0.1.0-dev.27`**",
    "README version",
)
readme = replace_once(
    readme,
    "Real dev25 post-reload acceptance reproduced the v2 model exactly in production: `25 ok / 5 declining / 1 weakening / 0 replace / 0 unavailable`, with 28 `ready`, 3 `limited`, all 31 devices `fresh`, and all seven guarded baseline-v2 records retained. Dev26 is release hardening only: it does not retune health thresholds or change the public condition contract.",
    "Real dev25 post-reload acceptance reproduced the v2 model exactly in production: `25 ok / 5 declining / 1 weakening / 0 replace / 0 unavailable`, with 28 `ready`, 3 `limited`, all 31 devices `fresh`, and all seven guarded baseline-v2 records retained. Dev26 release hardening was then accepted with the same production distribution. Dev27 is the final pre-RC contract cleanup: it removes temporary shadow/legacy runtime mirrors, makes deep discovery diagnostics disabled by default, shortens generated health entity IDs to source-derived `<battery source>_health`, and reduces public health attributes to the small set needed to explain the current state. Health thresholds and Store schemas are unchanged.",
    "README dev27 status",
)
readme = replace_once(
    readme,
    "Per-device health attributes include confidence, decision path, robust health\nmetrics, reasons, limitations, current guarded cycle generation and source entity\nreferences. These attributes remain visible in the current state but are marked\nunrecorded so only meaningful health-state transitions need Recorder history.",
    "Per-device health attributes are intentionally user-facing and small: trend,\nconfidence, calculation quality, the selected basis, current/reference values, the\nrelative change and a concise reason. A short note is added only when calculation\nquality is limited. Internal source IDs, cycle generations, raw reason codes and\nintermediate references stay in the disabled deep diagnostic sensor instead of the\nnormal health entity. Attributes remain unrecorded so only meaningful health-state\ntransitions need Recorder history.\n\nPer-device entity IDs are suggested as `sensor.<source battery object id without\n_battery>_health`, independent of Area and the user's global Entity ID format. A\none-time dev27 migration renames only entity IDs that still look like the old\nsystem-generated Battery Health Analyzer names; explicit user-customized IDs are\nleft untouched. The stable unique ID is unchanged.",
    "README public attributes",
)
legacy_heading = "## Legacy dev23 classifier (temporary diagnostic mirror)\n"
if legacy_heading in readme:
    start = readme.index(legacy_heading)
    end = readme.index("## Temperature source selection\n", start)
    readme = readme[:start] + readme[end:]
else:
    raise RuntimeError("README legacy classifier section not found")
readme = readme.replace(
    "Top-level diagnostics expose `health_v2_summary`, `health_v2_conditions`,\n`health_v2_without_condition`, `health_v2_calculation`, `health_v2_trends`,\n`health_v2_modes` and `health_v2_thresholds`.",
    "The disabled deep diagnostic sensor exposes canonical `health_summary`,\n`health_states`, `health_calculation`, `health_trends`, `health_modes`,\n`health_thresholds` and one full `health` diagnostic object per device. Temporary\n`health_shadow`, `health_v2_shadow`, `legacy_health_*`, `shadow_health_*` and\n`health_v2_*` parity aliases are removed in dev27.",
)
readme_path.write_text(readme)

public_test_path = ROOT / "tests/test_public_contract.py"
public_test_path.write_text('''"""Final pre-RC public naming and attribute contract tests."""

from __future__ import annotations

import unittest

from homeassistant.helpers.entity import EntityCategory

from custom_components.battery_health.health_v2 import RelativeHealthAssessment
from custom_components.battery_health.sensor import (
    BatteryHealthDiscoverySensor,
    _canonical_health_entity_id,
    _looks_like_generated_entity_id,
    _public_health_attributes,
)


class PublicContractTests(unittest.TestCase):
    """Keep final entity naming and visible attributes stable and small."""

    def test_health_entity_id_is_derived_from_source_battery_entity(self) -> None:
        self.assertEqual(
            _canonical_health_entity_id("sensor.dvere_balkon_battery", "abcdef"),
            "sensor.dvere_balkon_health",
        )
        self.assertEqual(
            _canonical_health_entity_id("sensor.custom_charge", "abcdef"),
            "sensor.custom_charge_health",
        )

    def test_generated_legacy_id_detection_preserves_custom_ids(self) -> None:
        self.assertTrue(
            _looks_like_generated_entity_id(
                "sensor.network_api_integrations_battery_health_analyzer_dvere_balkon_battery_health",
                "dvere_balkon Battery health",
            )
        )
        self.assertFalse(
            _looks_like_generated_entity_id(
                "sensor.my_custom_door_health",
                "dvere_balkon Battery health",
            )
        )

    def test_voltage_public_attributes_are_small_and_explanatory(self) -> None:
        assessment = RelativeHealthAssessment(
            "declining",
            "ready",
            "falling",
            0.64,
            "relative_voltage",
            "voltage_mv",
            2860.0,
            2900.0,
            2936.0,
            2936.0,
            2860.0 / 2936.0,
            -76.0,
            0.985,
            ("relative_voltage_decline",),
            (),
        )
        attrs = _public_health_attributes(assessment)
        self.assertEqual(attrs["basis"], "voltage")
        self.assertEqual(attrs["current"], 2860)
        self.assertEqual(attrs["reference"], 2936)
        self.assertEqual(attrs["change_unit"], "%")
        self.assertEqual(attrs["reason"], "Early persistent decline from own history")
        self.assertNotIn("assessment_mode", attrs)
        self.assertNotIn("source_device_id", attrs)
        self.assertNotIn("cycle_generation", attrs)
        self.assertNotIn("reasons", attrs)
        self.assertNotIn("limitations", attrs)

    def test_limited_battery_attributes_add_only_readable_note(self) -> None:
        assessment = RelativeHealthAssessment(
            "ok",
            "limited",
            "stable",
            0.5,
            "relative_battery_temperature_guard",
            "battery_percent",
            84.0,
            86.0,
            90.0,
            90.0,
            84.0 / 90.0,
            -6.0,
            0.0,
            ("relative_battery_stable",),
            ("temperature_sensitive_voltage_bypassed",),
        )
        attrs = _public_health_attributes(assessment)
        self.assertEqual(attrs["basis"], "battery_percent")
        self.assertEqual(attrs["change_from_reference"], -6.0)
        self.assertEqual(attrs["change_unit"], "pp")
        self.assertIn("temperature", attrs["note"].lower())

    def test_deep_discovery_sensor_is_disabled_diagnostic(self) -> None:
        self.assertEqual(
            BatteryHealthDiscoverySensor._attr_entity_category,
            EntityCategory.DIAGNOSTIC,
        )
        self.assertFalse(BatteryHealthDiscoverySensor._attr_entity_registry_enabled_default)


if __name__ == "__main__":
    unittest.main()
''')

print("dev27 transformation applied")
