# Battery Health Analyzer

Battery Health Analyzer is a Home Assistant custom integration for conservative
battery telemetry analysis **limited to entities provided by Home Assistant's
MQTT integration**.

The current primary target and validation environment is Zigbee2MQTT. The
integration deliberately does not discover battery entities owned by other Home
Assistant platforms such as `mobile_app`, `hue`, `zha`, `esphome` or vendor
integrations. This is an intentional project scope boundary, not a discovery
limitation to be worked around.

## Development status

**Current development version: `0.1.0-dev.12`**

The repository is in a read-only telemetry and evidence-profiling phase.
Discovery starts from Home Assistant Entity Registry entries whose `platform`
is exactly `mqtt`, then pairs battery percentage, battery voltage, `last_seen`,
`power_outage_count` and an optional safe same-device temperature source.

There is still no `ok`, `weakening`, `replace` or `unknown` health verdict.

Dev12 hardens `last_seen` startup handling: a valid live timestamp is preferred,
otherwise the latest valid Recorder timestamp is used. The operability Recorder
query requests full state changes so report-cadence learning is not dependent on
Home Assistant's significant-change filtering.

## Dev11 freshness and outage evidence

The diagnostic sensor is:

```text
sensor.battery_health_analyzer_discovered_devices
```

Dev11 turns the previously discovered `last_seen` and `power_outage_count`
entities into read-only evidence instead of exposing only their entity IDs.

`last_seen` is treated as an operability/freshness gate, not as proof of a
healthy battery. Diagnostics expose the current age plus device-specific report
cadence learned from the previous 24 hours:

```text
freshness:
  supported: true
  age_minutes: 1.0
  reports_24h: 18
  median_gap_minutes: 67.0
  p90_gap_minutes: 103.0
  age_to_p90_ratio: 0.01
  state: fresh
```

There is deliberately no universal one- or two-hour stale threshold. When at
least three cadence gaps are available, the current age is compared with that
device's p90 reporting gap. Dev11 exposes `fresh`, `late`, `stale`,
`insufficient`, `invalid` or `unavailable` for diagnostics only; these states do
not yet change battery health.

`power_outage_count` is optional and its absolute value is not health evidence.
Dev11 evaluates only reset-aware positive deltas during the previous 24 hours:

```text
power_outage:
  supported: true
  latest_count: 14
  events_24h: 2
  increment_transitions_24h: 2
  resets_24h: 0
```

A missing outage counter is neutral. Counter decreases are counted as resets,
not negative outages. Positive deltas are supporting instability evidence only;
no outage count can independently produce a future `replace` verdict.

For isolation during validation, dev11 currently reads `last_seen` and outage
counters in one additional lightweight 24-hour Recorder batch every 30 minutes.
This is intentionally separate from the existing battery/voltage batch until
real-device behavior is validated; consolidation is a later optimization.

## MQTT scope

Diagnostics expose:

```text
scope: mqtt_integration_only
source_platform: mqtt
```

Filtering uses the Entity Registry `platform` field, not entity naming,
`last_seen`, labels or device-name heuristics. This keeps the integration scope
stable even when an MQTT device does not expose every optional Zigbee2MQTT
telemetry entity.

## Telemetry profiler

The integration reads all selected battery and voltage entities in one 24-hour
Recorder batch every 30 minutes. The diagnostic output exposes time-weighted
p10/p50/p90, min/max/range, coverage, `history_rows` and `value_changes`.

`history_rows` is not treated as a physical packet count. Recorder restart or
restore rows can increase it without a real value change, while
`value_changes` counts only changes between valid numeric values.

On startup or integration reload, the profiler performs one additional batch
Recorder query for the previous 30 complete local calendar days. Those states
are reduced in memory to daily aggregates and used for:

- a 7-day stable upper envelope defined as the median of daily p90 values;
- 30-day battery behavior classification: `static`, `monotonic`, `volatile`,
  `mixed` or `insufficient`;
- battery-percentage to voltage correlation;
- optional voltage-to-temperature correlation.

Temperature is used only when discovery finds one unambiguous same-device MQTT
`sensor` with `device_class: temperature`. Setpoints, targets, calibration and
offset entities are excluded. An ambiguous or missing temperature source does
not affect core battery discovery.

Battery behavior and signal relationships are deliberately separate dimensions,
so a voltage-derived percentage is not counted as independent evidence.

## Baseline status

The baseline Store created during dev7/dev8 is preserved, but current learning
runs in shadow mode. Existing records are shown as `provisional`; proposed
learning changes are calculated for diagnostics but are not saved. Baseline
data must not be used for a health verdict until the profiler and evidence
model have been validated against real MQTT device output.

Battery percentage still uses the current HA state when it exists. During
startup, the latest state from the same Recorder query is used only when the
live state is absent. An existing or trailing `unknown`/`unavailable` state is
never skipped.

## Architecture status

1. Limit source discovery to Entity Registry platform `mqtt`. ✅ dev10
2. Discover and safely pair MQTT battery telemetry on the same HA device. ✅
3. Read and profile 24h / 7d / 30d battery and voltage telemetry. ✅ dev9
4. Add adaptive `last_seen` freshness evidence. 🧪 dev11/dev12
5. Add reset-aware 24h `power_outage_count` evidence. 🧪 dev11
6. Validate operability evidence on real Zigbee2MQTT devices.
7. Build the evidence/confidence model from validated telemetry channels.
8. Re-design guarded baseline learning from validated evidence.
9. Expose `ok`, `weakening`, `replace` or `unknown` per device.

Daily aggregates are intentionally not persisted yet. That optimization is
deferred until the profiling and evidence logic have been validated.

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

## Compatibility target

Home Assistant 2026.9 or newer.
