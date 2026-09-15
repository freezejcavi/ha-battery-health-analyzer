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

**Current development version: `0.1.0-dev.22`**

Dev21 introduced a **read-only shadow health engine** for calibration. It exposes
candidate `ok`, `weakening`, `replace` or `unknown` states only inside the
existing diagnostic sensor. These candidates are not production health entities,
do not write any health state to Home Assistant Store and do not alter guarded
baseline or cycle persistence.

Real dev21 Home Assistant validation on the complete 31-device MQTT population
produced `19 ok / 1 weakening / 0 replace / 11 unknown`. All 31 devices were
simultaneously `fresh`, Evidence Routing `ready` and current Cycle Integrity
`stable`. The single `weakening` candidate was `Teplota_Mrazák`; low/volatile
shared signals without a healthy anchor, including `Venkovní_sensor`, stayed
`unknown` as intended.

Dev22 hardens temperature-source discovery before any production health verdict.
When both a regular same-device `*_temperature` measurement and Zigbee2MQTT's
diagnostic `*_device_temperature` are present, the exact regular sibling is
preferred. Diagnostic device temperature remains a safe fallback when it is the
only temperature candidate; unrelated equally plausible temperature sources stay
ambiguous. Dev22 does **not** change health thresholds, persistence or cycle logic.

Discovery starts from Home Assistant Entity Registry entries whose `platform` is
exactly `mqtt`, then pairs battery percentage, battery voltage, `last_seen`,
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
rewrite an existing v2 record. Real Home Assistant acceptance including an
explicit integration reload confirmed seven stored generation-1 records survived
the unload/load roundtrip unchanged.

## Shadow health engine

Dev21 deliberately separates **candidate classification** from a future
production health verdict. The shadow result is exposed per device as
`health_shadow` and includes the candidate state, confidence, decision path,
metrics, reasons and limitations.

Safety gates run before any candidate classification:

- Evidence Routing must be `ready` and freshness must be `open` or `caution`;
- current Cycle Integrity must be `stable`;
- `possible_boundary`, `current_boundary` and insufficient cycle segments return
  `unknown`;
- `temperature_context: required` returns `unknown` until temperature-aware health
  normalization is designed and validated;
- shared/derived battery+voltage evidence without a guarded persisted baseline is
  intentionally `unknown` rather than falling back to the same battery percentage
  signal under another name.

For a guarded persisted continuous voltage baseline, the shadow engine compares
the robust **24-hour voltage p90** with the stored baseline:

- ratio >= `0.91` -> shadow `ok`;
- ratio >= `0.87` and < `0.91` -> shadow `weakening`;
- ratio < `0.87` -> shadow `replace`.

The voltage path uses 24h p90 rather than an instantaneous value or p50 to reduce
sensitivity to transient load dips. Confidence is bounded by persisted baseline
confidence, voltage-information confidence and current Recorder coverage. These
thresholds remain calibration hypotheses until the temperature-source hardening
is revalidated on the real population.

Battery-only fallback is intentionally weaker:

- a stable/high robust battery level (currently >= 75%) may become low-confidence
  shadow `ok`;
- a persistently low battery level (24h robust level <= 30% and 7d upper envelope
  <= 35%) with monotonic/mixed decline may become shadow `weakening`;
- mid-range, uncorroborated low or otherwise insufficiently calibrated battery-only
  evidence remains `unknown`;
- **battery-only evidence can never produce `replace` in the current shadow model**.

`power_outage_count` remains supporting evidence only. It never creates a health
state by itself; while its health weight is still uncalibrated, an outage signal
that conflicts with an otherwise `ok` candidate is conservatively returned as
`unknown`.

Example diagnostic shape:

```text
health_shadow:
  mode: shadow_no_publish
  candidate_state: ok
  confidence: 0.65
  decision_path: voltage_baseline
  metrics:
    voltage_health_ratio: 0.982
    battery_level_percent: 100.0
    baseline_mv: 3055
    current_voltage_p90_mv: 3000
  reasons:
    - voltage_ratio_ok
    - double_count_guard_applied
  limitations: []
```

Top-level diagnostics also expose `shadow_health_candidates`,
`shadow_health_paths` and the current calibration thresholds. No new user-facing
health entity exists in dev22.

## Temperature source selection

Temperature context is optional evidence but can block voltage-baseline learning
when a strong voltage-to-temperature relationship is observed. Source identity is
therefore safety-relevant.

Zigbee2MQTT distinguishes the regular `temperature` expose (measured temperature)
from `device_temperature`, which is diagnostic device telemetry. Dev22 uses that
semantic distinction conservatively:

- exact same-base `<battery source>_temperature` gets preference;
- `<battery source>_device_temperature` remains usable when it is the only safe
  same-device temperature candidate;
- unrelated equal-strength temperature candidates remain `ambiguous_temperature`;
- setpoint, target, calibration and offset entities remain excluded.

This preference is global and deterministic; there are no per-device overrides.

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
    state: retained
    changed: false
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

Temperature is used only when discovery finds one safe same-device MQTT
temperature source after the dev22 preference rules above.

## Legacy baseline status

The baseline Store created during dev7/dev8 is preserved for diagnostics only.
Its records remain `provisional`; legacy learning stays `shadow_no_save` and
must not feed the health engine. Dev20 does not migrate, overwrite or delete
those records.

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
12. Persist and reload guarded cycle/baseline-v2 state without downward learning. ✅ dev20
13. Build and validate a read-only shadow health classifier. ✅ dev21
14. Prefer regular measured temperature over diagnostic device temperature and revalidate temperature context. 🧪 dev22
15. Publish validated per-device `ok` / `weakening` / `replace` / `unknown` health entities.

Daily profiler aggregates are intentionally not persisted yet. Cadence samples
and guarded baseline-v2 records are persisted because they represent learned
state that cannot be reconstructed reliably from one current Recorder window.

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

Dev22 adds focused regression tests for regular-temperature preference, diagnostic
`device_temperature` fallback and preservation of true temperature ambiguity. The
current development tooling still does not provide a working repository clone,
so the full suite has not been physically executed here; real Home Assistant
output remains the acceptance gate for dev22.

## Compatibility target

Home Assistant 2026.9 or newer.
