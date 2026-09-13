# Battery Health Analyzer

Battery Health Analyzer is a Home Assistant custom integration that will
evaluate battery health from sustained voltage behaviour instead of trusting
the reported battery percentage alone.

## Development status

This repository is in the first read-only proof-of-concept phase. It discovers
battery-powered Home Assistant devices and pairs their battery percentage,
battery voltage, `last_seen`, binary low-battery and power-outage entities.

It does **not** calculate health or replace the current dashboard logic yet.

## Current proof of concept

After the integration is added through the UI, it exposes one diagnostic
sensor:

```text
sensor.battery_health_analyzer_discovered_devices
```

The sensor state is the number of discovered battery devices. Its diagnostic
attributes show the selected source entities, discovery issues and the exact
candidate entity IDs behind any ambiguous selection. All of its attributes are
excluded from Recorder.

## Planned architecture

1. Discover battery entities and pair related sources on the same HA device.
2. Read 24 hours of voltage history in one Recorder batch operation.
3. Calculate a time-weighted median and compare it with a learned baseline.
4. Expose `ok`, `weakening`, `replace`, or `unknown` per device.
5. Persist only the compact baseline and battery-cycle state.

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

## Compatibility target

Home Assistant 2026.9 or newer.
