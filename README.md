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

**Current development version: `0.1.0-dev.20`**

The repository now has a guarded persistent baseline-v2 Store, but there is still
no `ok`, `weakening`, `replace` or `unknown` health verdict. Discovery starts
from Home Assistant Entity Registry entries whose `platform` is exactly `mqtt`,
then pairs battery percentage, battery voltage, `last_seen`,
`power_outage_count` and an optional safe same-device temperature source.

Dev13-dev14 moved `last_seen` cadence learning away from Recorder and into a
compact live-learned Store after real Home Assistant validation showed that
Recorder timestamp history was not suitable for learning report cadence. Dev19
made that Store time-balanced: at most one representative timestamp is retained
per fixed 15-minute UTC bucket, preventing high-rate devices from collapsing the
intended seven-day horizon. Real dev19 validation produced 31/31 `fresh`, 31/31
Evidence Routing `ready` and 31/31 current Cycle Integrity `stable` devices.

Dev15 introduced read-only Evidence Routing. Dev16 added Cycle Integrity. Dev17
added guarded baseline-v2 assessment and historical cycle segmentation. Dev18
hardened both layers after real validation exposed double-counting of coupled
battery percentage and voltage plus a missing required-temperature guard.

Dev20 moves Evidence Routing, Cycle Integrity and guarded baseline-v2 assessment
out of the diagnostic sensor and into the coordinator, then adds a **separate**
guarded Store at `battery_health.baselines_v2`. The older dev7/dev8 Store remains
untouched and provisional. Only an `eligible` v2 assessment may create or alter a
v2 record. `learning`, `blocked` and `not_required` assessments never delete or
rewrite an existing v2 record.

## Guarded baseline-v2 persistence

The persistent v2 record is intentionally conservative:

- the first eligible assessment creates `cycle_generation: 1`;
- within the same cycle the stored baseline can only rise materially (at least
  1%); it never learns downward;
- a `possible_boundary`, stale/blocked evidence, missing healthy anchor or
  required temperature context cannot alter the Store;
- a new cycle generation is created only after an `eligible` **segmented**
  assessment exposes a different confirmed `boundary_date`;
- seeing the same boundary again does not increment the generation twice;
- legacy baseline records are not migrated into v2 automatically.

A left-censored bootstrap is therefore explicitly different from a known cycle
start. It may be persisted when eligible, but its confidence remains capped by
the guarded baseline model. Later detection of a genuinely confirmed independent
cycle boundary starts a new generation only after enough clean post-boundary
history exists to become eligible.

Example diagnostic shape:

```text
baseline_v2:
  mode: guarded_assessment
  eligibility: eligible
  confidence: 0.65
  candidate:
    voltage_mv: 3055
    source: cycle_segment_upper_envelope
    persisted: false
  anchor: battery_upper_bootstrap
  cycle_segment:
    state: left_censored
  persistence:
    state: created
    changed: true
    persisted: true
    record:
      baseline_mv: 3055
      confidence: 0.65
      cycle_generation: 1
      boundary_date: null
      cycle_start_known: false
```

The `candidate.persisted` flag belongs to the pure assessment payload and remains
false; actual persistence is represented separately by the `persistence` block.
This keeps assessment and Store state distinguishable.

## Cycle integrity

Each device exposes a `cycle_integrity` diagnostic block. The classifier compares
its current 24-hour regime with the median of the previous seven complete daily
p90 values. A boundary is considered probable only when a persistent battery
upshift and an informative-voltage upshift occur together **and** Evidence
Routing has established that those channels are independent. A joint upshift in
coupled/shared/unknown channels is only `possible_boundary`; it must never count
the same physical signal twice.

A much larger single-channel upshift may also be marked only as
`possible_boundary` when the other channel is unavailable or low-information.
Current or historical possible boundaries quarantine baseline learning instead
of creating a new cycle.

## Evidence routing

Each discovered device exposes an `evidence_model` diagnostic block. The model
decides how telemetry may be used later; it does not itself score battery health.
It determines:

- whether freshness leaves decision evidence open, limited or blocked;
- whether battery percentage behaves as a level, trend, robust upper envelope or
  mixed signal;
- whether voltage is `continuous`, `quantized`, `static` or `insufficient`;
- whether battery percentage and voltage are independent, coupled, shared or
  still unknown;
- whether temperature context is required, relevant, optional or unavailable;
- whether `power_outage_count` is neutral or only supporting an escalation.

`decision_readiness` is not a health verdict.

## Freshness and outage evidence

The diagnostic sensor is:

```text
sensor.battery_health_analyzer_discovered_devices
```

`last_seen` is an operability/freshness gate, not proof of a healthy battery.
Diagnostics expose the current age and device-specific learned cadence:

```text
freshness:
  supported: true
  last_seen: 2026-09-15T05:05:18+00:00
  source: cadence_store
  age_minutes: 17.2
  reports_24h: 22
  cadence_samples: 21
  median_gap_minutes: 55.0
  p90_gap_minutes: 55.3
  age_to_p90_ratio: 0.31
  state: fresh
  reports_24h_semantics: time_balanced_cadence_points
  sampling_interval_minutes: 15
```

There is deliberately no universal one- or two-hour stale threshold. When at
least three cadence gaps are available, current age is compared with the
learned device-specific p90 gap.

From dev19 the cadence Store keeps at most one representative timestamp per
fixed 15-minute UTC bucket over a seven-day retention window. The 768-point hard
cap leaves headroom above the approximately 672 buckets needed for seven complete
days. Existing dev13-dev18 `timestamps` data is loaded through the same schema
and compacted automatically.

`reports_24h` means retained **time-balanced cadence points**, not physical
Zigbee packets or raw MQTT reports. It is diagnostic only and is not a health
weight. Live `last_seen` changes immediately refresh freshness and the derived
evidence/cycle/baseline assessment, but do not write the baseline-v2 Store or
launch a full Recorder/profiler cycle.

`power_outage_count` is optional. Its absolute value is not health evidence; only
reset-aware positive deltas during the previous 24 hours are supporting evidence.
A missing counter is neutral.

## MQTT scope

Diagnostics expose:

```text
scope: mqtt_integration_only
source_platform: mqtt
```

Filtering uses the Entity Registry `platform` field, not naming, `last_seen`,
labels or device-name heuristics.

## Telemetry profiler

The integration reads selected battery and voltage entities in one 24-hour
Recorder batch every 30 minutes. Diagnostics expose time-weighted p10/p50/p90,
min/max/range, coverage, `history_rows` and `value_changes`.

On startup or integration reload, one additional batch Recorder query reads the
previous 30 complete local calendar days and reduces them to daily aggregates for:

- a 7-day stable upper envelope defined as the median of daily p90 values;
- 30-day battery behavior classification: `static`, `monotonic`, `volatile`,
  `mixed` or `insufficient`;
- battery-percentage to voltage correlation;
- optional voltage-to-temperature correlation.

Temperature is used only when discovery finds one unambiguous safe same-device
MQTT temperature sensor. Setpoints, targets, calibration and offset entities are
excluded.

## Legacy baseline status

The baseline Store created during dev7/dev8 is preserved for diagnostics only.
Its records remain `provisional`; legacy learning stays `shadow_no_save` and
must not feed the future health verdict. Dev20 does not migrate, overwrite or
delete those records.

## Architecture status

1. Limit source discovery to Entity Registry platform `mqtt`. ✅ dev10
2. Discover and safely pair MQTT battery telemetry on the same HA device. ✅
3. Read and profile 24h / 7d / 30d battery and voltage telemetry. ✅ dev9
4. Add adaptive `last_seen` freshness evidence. ✅ dev11-dev14
5. Add reset-aware 24h `power_outage_count` evidence. ✅ dev11
6. Validate live-learned cadence and persistence on real Zigbee2MQTT devices. ✅ dev14
7. Build and validate the evidence-routing model. ✅ dev15
8. Detect recent battery-cycle boundaries before baseline learning. ✅ dev16
9. Build guarded baseline v2 from cycle-clean evidence. ✅ dev17
10. Enforce topology de-duplication and required-temperature guards. ✅ dev18
11. Time-balance bounded cadence history for high-rate devices. ✅ dev19
12. Persist guarded cycle/baseline-v2 state without downward learning. 🧪 dev20
13. Build and validate the final `ok` / `weakening` / `replace` / `unknown` health engine.

Daily profiler aggregates are intentionally not persisted yet. Cadence samples
and guarded baseline-v2 records are persisted because they represent learned
state that cannot be reconstructed reliably from one current Recorder window.

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

## Compatibility target

Home Assistant 2026.9 or newer.
