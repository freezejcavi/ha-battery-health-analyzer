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

**Current development version: `0.1.0-dev.18`**

The repository is in a read-only telemetry, evidence-routing, cycle-integrity
and guarded-baseline validation phase. Discovery starts from Home Assistant
Entity Registry entries whose `platform` is exactly `mqtt`, then pairs battery
percentage, battery voltage, `last_seen`, `power_outage_count` and an optional
safe same-device temperature source.

There is still no `ok`, `weakening`, `replace` or `unknown` health verdict.

Dev13 moved `last_seen` cadence learning away from Recorder after real Home
Assistant validation showed that timestamp history exposed only one usable state
in 24 hours while the live MQTT timestamp updated correctly. Dev14 keeps that
live-learning model and additionally publishes freshness immediately when a
`last_seen` state changes, without triggering the expensive Recorder/profiler
refresh. Learned cadence may use the rolling 7-day Store while `reports_24h`
remains strictly bounded to the previous 24 hours.

Dev15 adds a read-only Evidence Routing Model. It does **not** score battery
health. Instead it decides how telemetry channels may be used later: freshness
acts as a gate, battery and voltage are de-duplicated when strongly coupled,
voltage is classified as `continuous`, `quantized`, `static` or `insufficient`,
and outage data remains supporting evidence only.

Dev16 adds a read-only Cycle Integrity gate. Real dev15 validation exposed a
device whose current 24-hour regime was materially above its previous seven-day
reference in both battery percentage and voltage. That is exactly the situation
where a new battery cycle may have started and old 7d/30d aggregates must not be
used for baseline learning without segmentation. Dev16 therefore detects only
conservative recent regime upshifts; it does not increment a cycle or alter the
health state.

Dev17 adds guarded baseline v2 in read-only `shadow_no_save` mode. It segments
long-term daily history at conservative cycle boundaries, excludes pre-cycle
history, distinguishes `eligible`, `learning`, `blocked` and `not_required`, and
builds baseline confidence from the weakest required evidence dimension. It does
not write the baseline Store, increment a battery cycle, or issue a health
verdict.

Dev18 hardens that contract after real dev17 validation exposed a false cycle
boundary: battery percentage and voltage can move together because the percentage
is derived from the same physical voltage signal. Cycle Integrity now promotes a
joint persistent upshift to `probable_boundary` only when Evidence Routing has
classified battery and voltage as independent. Coupled, shared or unknown joint
upshifts remain quarantined as `possible_boundary`. Guarded baseline v2 also
blocks learning when voltage interpretation requires temperature context.

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

Example shape:

```text
cycle_integrity:
  state: probable_boundary
  history_usable: false
  battery:
    reference_7d_percent: 65.0
    reference_days: 7
    upshift_pp: 35.0
    signal: persistent_upshift
  voltage:
    reference_7d_mv: 2948
    reference_days: 7
    upshift_p50_mv: 289.0
    upshift_floor_mv: 171.0
    signal: persistent_upshift
  reasons:
    - independent_joint_persistent_upshift
```

The thresholds remain deliberately conservative diagnostic hypotheses. They must
continue to be validated against the real MQTT population before any persistent
battery-cycle change or baseline reset is allowed.

## Evidence routing

Each discovered device exposes an `evidence_model` diagnostic block. The model
answers questions such as:

- is operability evidence open, limited or blocked by freshness;
- should battery percentage be interpreted as a level, trend or robust upper
  envelope;
- does voltage carry continuous information, only coarse quantized levels, or
  effectively no changing information;
- are battery percentage and voltage independent condition channels or the same
  underlying signal;
- is temperature context required/relevant/optional;
- is `power_outage_count` neutral or supporting an instability escalation.

Example shape:

```text
evidence_model:
  freshness_gate: open
  decision_readiness: ready
  condition_channels:
    independent_count: 1
    double_count_guard: true
  battery:
    role: shared
    processing: upper_envelope
  voltage:
    role: shared
    information:
      type: continuous
      confidence: 1.0
  battery_voltage_topology: shared
  temperature_context: optional
  outage_role: unavailable
```

`decision_readiness` means only that the evidence channels are sufficiently
understood to permit a later health decision. It is **not** a health verdict.

## Freshness and outage evidence

The diagnostic sensor is:

```text
sensor.battery_health_analyzer_discovered_devices
```

`last_seen` is treated as an operability/freshness gate, not as proof of a
healthy battery. Diagnostics expose the current age plus device-specific report
cadence learned from live timestamp changes:

```text
freshness:
  supported: true
  last_seen: 2026-09-14T11:42:09+00:00
  source: current
  age_minutes: 22.2
  reports_24h: 8
  cadence_samples: 7
  median_gap_minutes: 66.0
  p90_gap_minutes: 102.0
  age_to_p90_ratio: 0.22
  state: fresh
```

There is deliberately no universal one- or two-hour stale threshold. When at
least three cadence gaps are available, the current age is compared with that
device's p90 reporting gap. Until enough live samples are learned, freshness is
`insufficient`; this is expected after first installation and does not imply a
battery problem.

The live cadence Store keeps at most 512 timestamps per device with a 7-day
retention window and uses delayed writes to avoid unnecessary storage churn.
On integration unload the compact Store is flushed immediately. A valid live
`last_seen` value is preferred; if live state is temporarily unavailable, the
latest learned Store timestamp can be used as a fallback. Live `last_seen`
changes update only the freshness evidence and diagnostic entity; they do not
launch a full 24h/30d analysis cycle.

`power_outage_count` is optional and its absolute value is not health evidence.
Only reset-aware positive deltas during the previous 24 hours are evaluated:

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

The baseline Store created during dev7/dev8 is preserved, but current legacy
learning runs in shadow mode. Existing records are shown as `provisional` and
must not be used for a health verdict.

Guarded baseline v2 is a separate diagnostic path. A voltage baseline is
considered only for informative voltage channels routed by the Evidence Model.
Long-term history is segmented by Cycle Integrity before it can contribute to a
candidate. Current or possible boundaries remain in learning/blocked states,
static/context-only voltage is reported as `not_required`, and
`temperature_context: required` blocks voltage baseline learning until that
context can be safely applied. All candidates remain diagnostic only and
`persisted: false`.

Battery percentage still uses the current HA state when it exists. During
startup, the latest state from the same Recorder query is used only when the
live state is absent. An existing or trailing `unknown`/`unavailable` state is
never skipped.

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
10. Enforce topology de-duplication and required-temperature guards. 🧪 dev18
11. Persist verified cycle/baseline state only after real validation.
12. Expose `ok`, `weakening`, `replace` or `unknown` per device.

Daily profiler aggregates are intentionally not persisted yet. Cadence samples
are persisted separately because real validation showed that Recorder is not a
reliable source of `last_seen` report cadence in this environment.

## Local verification

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
```

## Compatibility target

Home Assistant 2026.9 or newer.
