# Changelog

## 0.1.0-rc.1

First release candidate of Battery Health Analyzer.

### Public contract

- MQTT-integration-only discovery, with Zigbee2MQTT as the primary validated environment.
- One enum health sensor per discovered MQTT battery device.
- Production condition states: `ok`, `declining`, `weakening`, `replace`.
- True lack of trustworthy calculable condition maps to Home Assistant `unavailable`; `unknown` is not a battery-condition state.
- Aggregate `sensor.battery_health_summary` with precedence `replace > weakening > declining > ok`.
- Source-derived health entity IDs such as `sensor.dvere_balkon_health`, while stable unique IDs remain unchanged.
- Conservative one-time migration of old system-generated health entity IDs; explicitly customized IDs are preserved.
- Compact public attributes: trend, confidence, calculation quality, selected basis, current/reference values, relative change, reason and optional readable limitation note.

### Health model and safety

- Relative Health Model v2 evaluates robust current telemetry against each device's own history rather than trusting instantaneous percentage.
- Evidence Routing prevents double-counting coupled battery percentage and voltage.
- Device-specific adaptive `last_seen` freshness learned from live Home Assistant state changes.
- Time-balanced seven-day cadence Store for high-rate and sparse devices.
- Cycle Integrity guards baseline learning around possible battery replacements.
- Guarded baseline-v2 persistence never learns downward within the same cycle and only creates a new generation after an eligible confirmed segmented cycle.
- Safe temperature-source preference favors measured `temperature` over Zigbee2MQTT diagnostic `device_temperature`.
- Temperature-sensitive and cycle-limited cases remain explicitly `limited` rather than being presented with false precision.

### Diagnostics and packaging

- Deep `Discovered devices` diagnostics are `EntityCategory.DIAGNOSTIC` and disabled by default for new registry entries.
- Canonical diagnostics only; temporary legacy/shadow parity aliases have been removed.
- Config flow, English/Czech translations, `single_config_entry`, HACS metadata and validation workflows are included.
- CI covers repository compile, Ruff, unit tests, Hassfest and HACS validation.

### Accepted pre-RC runtime snapshot

The final `0.1.0-dev.27` acceptance on the primary 31-device validation installation produced:

- 25 `ok` / 5 `declining` / 1 `weakening` / 0 `replace` / 0 unavailable;
- Summary `weakening`;
- 31/31 `fresh`;
- 31/31 Evidence Routing `ready`;
- 31/31 current Cycle Integrity `stable`;
- 7 guarded baseline-v2 records retained / 24 not persisted;
- 33 integration entities total: 31 health + Summary + Discovered devices;
- no old duplicate health entity IDs;
- canonical deep diagnostics without legacy/shadow aliases.

These counts describe the validation installation and are not expected values for other homes.

### Known limitations

- MQTT integration only.
- Conservative thresholds are not chemistry-specific and are not yet calibrated on a broad ground-truth replacement dataset.
- Full temperature normalization is not implemented.
- No prediction of remaining battery days.
- Daily profiler aggregates are reconstructed from Recorder instead of persisted.

RC1 intentionally contains no Health Model v2 threshold, Store schema, freshness, cycle or discovery-logic retuning relative to accepted dev27.
