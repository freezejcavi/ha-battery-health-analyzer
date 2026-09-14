# Battery Health Analyzer

Battery Health Analyzer is a Home Assistant custom integration that will
evaluate battery health from sustained voltage behaviour instead of trusting
the reported battery percentage alone.

## Development status

This repository is in a read-only proof-of-concept phase. It discovers
battery-powered Home Assistant devices, pairs their source entities and reads
all selected voltage histories from Recorder in one 24-hour batch operation.

It calculates a diagnostic time-weighted voltage median and coverage, but does
not yet expose health verdicts or replace the current dashboard logic.

## Current proof of concept

After the integration is added through the UI, it exposes one diagnostic
sensor:

```text
sensor.battery_health_analyzer_discovered_devices
```

The sensor state is the number of discovered battery devices. Its diagnostic
attributes show the selected source entities, discovery issues and the exact
candidate entity IDs behind any ambiguous selection. For each selected voltage
source, `voltage_history` contains `median_24h_mv`, valid-time coverage and a
compact status. Historical state attributes provide the source unit, with the
current state used only as a fallback. All diagnostic attributes are excluded
from Recorder.

The baseline PoC persists one compact record per device. It starts learning
only from a window with at least 75% coverage and a current battery report of
at least 80%. A learned baseline can rise but never automatically fall with a
weakening battery. The diagnostic output exposes the learning state and
confidence; it does not produce a health verdict yet.

Voltage discovery uses the stable sibling entity-ID base as its fallback, so
temporarily unavailable sleeping devices do not disappear from the pairing
result and unrelated actuator voltage diagnostics are not mistaken for battery
voltage.

## Planned architecture

1. Discover battery entities and pair related sources on the same HA device.
2. Read 24 hours of voltage history in one Recorder batch operation. ✅ PoC
3. Learn and persist a guarded per-device healthy baseline. ✅ PoC
4. Expose `ok`, `weakening`, `replace`, or `unknown` per device.
5. Persist only the compact baseline and battery-cycle state.

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

## Compatibility target

Home Assistant 2026.9 or newer.
