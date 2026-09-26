# Changelog

## 0.1.0

First stable release of Battery Health Analyzer. This release promotes the accepted
`0.1.0-rc.3` behavior without changing runtime semantics.

### Stable acceptance

- The RC3 telemetry-integrity persistence and recovery behavior was accepted in the
  real Home Assistant validation installation without known operational issues.
- Release-candidate CI passed repository compile, full Ruff and **158/158 unit tests**,
  with Hassfest and HACS validation also green.
- No Health Model v2 threshold retuning was introduced for stable.

### Stable contract

- Production battery-health states remain `ok`, `declining`, `weakening` and
  `replace`.
- Source-derived public health entity IDs and stable unique IDs are unchanged.
- Severe telemetry-integrity incidents remain persistently latched across rolling
  analysis windows and restarts until recovery is proven by three distinct newer clean
  device reports.
- Guarded baseline-v2 persistence, adaptive freshness, cycle integrity, temperature
  context guards and MQTT-only discovery remain unchanged from RC3.
- Deep legacy/shadow runtime branches remain removed.

### Compatibility

- Home Assistant compatibility target remains 2026.9 or newer.
- Existing cadence, baseline-v2 and integrity-incident Store formats are unchanged.
- Stable 0.1.0 introduces no entity-ID migration, health-threshold change or feature
  expansion beyond the accepted RC3 public contract.

## 0.1.0-rc.3

Third release candidate focused on persistence and recovery of severe telemetry-integrity incidents.

### Persistent integrity incident latch

- Severe `service_required` findings are now persisted in a dedicated compact Home Assistant Store.
- Once latched, the public health state remains `replace` even after the original anomaly leaves the rolling 24-hour analysis window.
- Repeated analysis of the same stale `last_seen` cannot advance recovery.
- Baseline-v2 persistence remains blocked for the full lifetime of an active integrity incident.

### Seven-day outage bootstrap

- Recorder outage-counter history is queried across a seven-day incident lookback while normal operational outage totals remain 24-hour evidence.
- The largest positive outage-counter delta is retained for both 24-hour and seven-day windows.
- A recent RC3 installation can therefore bootstrap an incident that occurred before installation, including the validated `Rad_E1_Adi_pokoj` case where `power_outage_count` jumped from 5 to 26655.

### Recovery semantics

- An active incident clears only after **3 distinct newer clean device reports**.
- A new report with a current integrity problem resets the recovery streak.
- The historical seven-day outage marker by itself is tolerated during recovery, so a physically serviced device does not remain latched merely because the original outage anomaly is still present in the bootstrap lookback.
- If the device stops reporting, the incident remains latched instead of aging away.

### Diagnostics and regression coverage

- Deep diagnostics expose the active incident record, recovery progress and latest incident transition.
- Added tests for stale-report non-recovery, three-report recovery, recovery reset, Store round-trip, and retained seven-day outage escalation.
- Public health states remain unchanged: `ok`, `declining`, `weakening`, `replace`.

### Compatibility

- Adds a new independent Store key for active integrity incidents; existing baseline Store schemas are unchanged.
- Health Model v2 thresholds, MQTT-only discovery scope and public entity IDs remain unchanged.
- Home Assistant compatibility target remains 2026.9 or newer.

## 0.1.0-rc.2

Second release candidate focused on runtime telemetry integrity and safer service escalation.

### Telemetry Integrity

- Adds a cross-signal Telemetry Integrity layer before Health Model v2.
- Classifies input telemetry as `trusted`, `guarded` or `service_required`.
- Detects implausible diagnostic temperature sentinels, large `power_outage_count` jumps and abrupt battery-percentage collapse relative to the device's own history.
- Tracks the largest positive outage-counter delta in the 24-hour evidence window so one corrupted counter jump cannot disappear inside an aggregate total.
- Treats missing optional voltage, temperature or outage channels as unavailable evidence rather than as an integrity failure.

### Service-oriented `replace`

- Severe contradictory telemetry can now publish `replace` with `calculation: limited` and `basis: telemetry_integrity`.
- In this path, `replace` means **battery service / physical inspection required**. It does not claim that every cell is mathematically proven exhausted.
- This intentionally matches the operational workflow: inspect the device, measure individual cells and contacts, and replace only the weak or failed cell(s) when appropriate.
- Isolated anomalies remain conservative and do not escalate directly to `replace`.

### Persistence safety

- Baseline-v2 persistence is blocked whenever telemetry integrity is not `trusted`.
- Guarded or service-required telemetry cannot create, raise or otherwise contaminate the persisted battery baseline.
- Existing persisted baseline records are retained unchanged while integrity is guarded.

### Regression coverage

- Adds a regression fixture for the real `Rad_E1_Adi_pokoj` anomaly:
  - battery percentage 100% -> 0%;
  - voltage remaining at 3100 mV;
  - diagnostic temperature `-327.7 °C`;
  - `power_outage_count` jump 5 -> 26655.
- Adds coverage for isolated battery collapse, temperature sentinel, huge outage jump, missing optional channels and Health Model v2 integrity routing.
- Keeps the four-state public health contract unchanged: `ok`, `declining`, `weakening`, `replace`.

### Compatibility

- No Store schema change.
- No Health Model v2 threshold retuning.
- No widening beyond MQTT integration discovery.
- Home Assistant compatibility target remains 2026.9 or newer.

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
