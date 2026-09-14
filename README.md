# Battery Health Analyzer

Battery Health Analyzer is a Home Assistant custom integration that evaluates
battery telemetry conservatively instead of trusting one reported percentage or
voltage value as ground truth.

## Development status

The repository is in a read-only telemetry-profiling phase. The integration
automatically discovers battery devices and pairs battery percentage, battery
voltage, `last_seen`, `power_outage_count` and an optional safe same-device
temperature source.

There is still no `ok`, `weakening`, `replace` or `unknown` health verdict.

## Dev9 telemetry profiler

The diagnostic sensor is:

```text
sensor.battery_health_analyzer_discovered_devices
```

The integration reads all selected battery and voltage entities in one 24-hour
Recorder batch every 30 minutes. The diagnostic output exposes time-weighted
p10/p50/p90, min/max/range, coverage, `history_rows` and `value_changes`.

`history_rows` is not treated as a physical packet count. Recorder restart or
restore rows can increase it without a real value change, while
`value_changes` counts only changes between valid numeric values.

On startup or integration reload, dev9 performs one additional batch Recorder
query for the previous 30 complete local calendar days. Those states are
reduced in memory to daily aggregates and used for:

- a 7-day stable upper envelope defined as the median of daily p90 values;
- 30-day battery behavior classification: `static`, `monotonic`, `volatile`,
  `mixed` or `insufficient`;
- battery-percentage to voltage correlation;
- optional voltage-to-temperature correlation.

Temperature is used only when discovery finds one unambiguous same-device
`sensor` with `device_class: temperature`. Setpoints, targets, calibration and
offset entities are excluded. An ambiguous or missing temperature source does
not affect core battery discovery.

Battery behavior and signal relationships are deliberately separate dimensions,
so a voltage-derived percentage is not counted as independent evidence.

## Baseline status

The baseline Store created during dev7/dev8 is preserved, but dev9 runs baseline
learning in shadow mode. Existing records are shown as `provisional`; proposed
learning changes are calculated for diagnostics but are not saved. Baseline
data must not be used for a health verdict until the profiler has been
validated against real Home Assistant output.

Battery percentage still uses the current HA state when it exists. During
startup, the latest state from the same Recorder query is used only when the
live state is absent. An existing or trailing `unknown`/`unavailable` state is
never skipped.

## Architecture status

1. Discover battery entities and pair related sources on the same HA device. ✅
2. Read 24-hour battery and voltage history in one Recorder batch. ✅
3. Expose robust 24-hour telemetry statistics. ✅
4. Build read-only 7-day / 30-day telemetry profiles. ✅ dev9
5. Validate profiler behavior on real devices.
6. Re-design guarded baseline learning from validated telemetry behavior.
7. Expose `ok`, `weakening`, `replace` or `unknown` per device.

Daily aggregates are intentionally not persisted in dev9. That optimization is
deferred until the profiling logic has been validated.

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

## Compatibility target

Home Assistant 2026.9 or newer.
