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

**Current development version: `0.1.0-dev.27`**

Dev21 introduced a read-only shadow health classifier and real Home Assistant
validation on the complete 31-device MQTT population produced
`19 ok / 1 weakening / 0 replace / 11 unknown`. All 31 devices were simultaneously
`fresh`, Evidence Routing `ready` and current Cycle Integrity `stable`. The single
`weakening` candidate was `Teplota_Mrazák`.

Dev22 hardened temperature-source discovery and was accepted on the real
31-device population. Temperature ambiguity fell to zero. Regular measured
`*_temperature` is selected ahead of Zigbee2MQTT diagnostic
`*_device_temperature` when both exist. This exposed the expected strong
voltage-to-temperature relationship on `Venkovní_sensor` (`temperature_context:
required`).

Dev23 promoted the validated classifier unchanged to Home Assistant entities.
The health calculation runs once in the coordinator and is consumed both by
diagnostics and by production sensors. Dev23 adds one enum health sensor per
discovered MQTT battery device plus one aggregate summary sensor. Real post-reload
validation confirmed 31 per-device entities, 7 retained baseline-v2 records and
full parity between production and shadow outputs.

Dev24 is a **parallel read-only Health Model v2** prompted by real use of the
published entities. The key finding is that `unknown` mixes two different
questions: battery condition and ability to calculate it. This is especially
misleading for strongly derived/volatile devices such as Aqara Motion Sensor P1,
where an instantaneous percentage can move from 0 to tens of percent while the
robust voltage regime remains close to its own recent history.

Health Model v2 therefore separates:

- condition: `ok / declining / weakening / replace`;
- calculation quality: `ready / limited / unavailable`;
- trend: `stable / falling / recovering / volatile / insufficient`;
- confidence and assessment mode.

Real dev24 validation on all 31 MQTT devices produced `25 ok / 5 declining /
1 weakening / 0 replace`, `health_v2_without_condition = 0`, 28 `ready` and 3
explicitly `limited` calculations. All former 11 production `unknown` cases became
calculable conditions without introducing any new `weakening` or `replace` state.
Dev25 therefore promotes Health Model v2 to the existing production entity IDs.
The dev23 classifier remains available only as a temporary legacy diagnostic mirror.

Real dev25 post-reload acceptance reproduced the v2 model exactly in production: `25 ok / 5 declining / 1 weakening / 0 replace / 0 unavailable`, with 28 `ready`, 3 `limited`, all 31 devices `fresh`, and all seven guarded baseline-v2 records retained. Dev26 release hardening was then accepted with the same production distribution. Dev27 is the final pre-RC contract cleanup: it removes temporary shadow/legacy runtime mirrors, makes deep discovery diagnostics disabled by default, shortens generated health entity IDs to source-derived `<battery source>_health`, and reduces public health attributes to the small set needed to explain the current state. Health thresholds and Store schemas are unchanged.

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

Dev15 introduced Evidence Routing. Dev16 added Cycle Integrity. Dev17 added
guarded baseline-v2 assessment and historical cycle segmentation. Dev18 hardened
both layers after real validation exposed double-counting of coupled battery
percentage and voltage plus a missing required-temperature guard.

Dev20 moved Evidence Routing, Cycle Integrity and guarded baseline-v2 assessment
out of the diagnostic sensor and into the coordinator, then added a **separate**
guarded Store at `battery_health.baselines_v2`. The older dev7/dev8 Store remains
untouched and provisional. Only an `eligible` v2 assessment may create or alter a
v2 record. `learning`, `blocked` and `not_required` assessments never delete or
rewrite an existing v2 record. Real Home Assistant acceptance including an
explicit integration reload confirmed seven stored generation-1 records survived
the unload/load roundtrip unchanged.

## Published health entities

Dev25 publishes Health Model v2 through the existing per-device entity IDs. Each
discovered MQTT battery device has one enum sensor with these production states:

- `ok`
- `declining`
- `weakening`
- `replace`

The entity uses Home Assistant's enum sensor model. The health entity is a primary
entity, not a diagnostic entity. Its unique ID is based on the config entry plus
the stable Home Assistant `device_id`; no `default_entity_id` or per-device manual
override is used.

Per-device health attributes are intentionally user-facing and small: trend,
confidence, calculation quality, the selected basis, current/reference values, the
relative change and a concise reason. A short note is added only when calculation
quality is limited. Internal source IDs, cycle generations, raw reason codes and
intermediate references stay in the disabled deep diagnostic sensor instead of the
normal health entity. Attributes remain unrecorded so only meaningful health-state
transitions need Recorder history.

Per-device entity IDs are suggested as `sensor.<source battery object id without
_battery>_health`, independent of Area and the user's global Entity ID format. A
one-time dev27 migration renames only entity IDs that still look like the old
system-generated Battery Health Analyzer names; explicit user-customized IDs are
left untouched. The stable unique ID is unchanged.

The aggregate `Summary` sensor uses v2 actionable precedence:

```text
replace > weakening > declining > ok
```

If an individual condition truly cannot be calculated, that entity is Home
Assistant `unavailable`; `unknown` is not a battery-condition state. Summary stays
available while at least one device has a calculable condition and exposes an
`unavailable_devices` list.

## Health Model v2 (dev25 production)

The v2 model uses the **best available condition signal relative to the device's
own history**. Instantaneous values are diagnostic context, not the primary
condition input.

For informative continuous voltage the model uses:

- current robust 24-hour voltage p90;
- recent 7-day median of complete-day p90 values;
- a 30-day robust upper reference defined as the median of the highest three
  complete-day p90 values;
- recent direction from adjacent three-day robust blocks;
- an existing guarded persisted baseline when it is valid for the current cycle.

When informative voltage is missing, static, quantized or otherwise not the best
condition channel, battery percentage uses the **same relative-history pattern**:
current 24h p90, recent 7d reference, robust 30d upper reference and recent trend.
Percentage is not assumed to be literal remaining lifetime. A low instantaneous
percentage therefore cannot create a severe state by itself.

The production condition states are:

- `ok`: robust current regime remains close to its own reference without a
  material persistent decline;
- `declining`: an early, informational deterioration relative to own history;
- `weakening`: a material and persistent decline;
- `replace`: a deep persistent decline with corroborating multi-day evidence.

For voltage, the initial calibration hypotheses are 0.98 / 0.94 / 0.90 relative
to the selected reference for declining / weakening / replace. For battery-only
assessment, deterioration is expressed mainly in percentage-point change relative
to the device's own observed upper regime; absolute low percentages are only
supporting evidence.

Volatile percentage channels use a multi-day envelope guard. A one-off drop such
as `80 -> 80 -> 20 -> 79` must not create a battery-health deterioration. A
persistent progression such as `80 -> 76 -> 72 -> 67 -> 61` may.

Temperature-sensitive shared voltage remains calculable but is explicitly marked
`limited` and uses a short-window guarded relative comparison. Aggressive
escalation is capped until temperature normalization is designed and validated.
When a separate primary battery percentage channel is safer, the model bypasses
the temperature-sensitive voltage for condition assessment.

Cycle ambiguity no longer automatically becomes a battery condition called
`unknown`. Instead it limits confidence and aggressive escalation. A confirmed
segmented new cycle filters pre-cycle history out of the v2 reference.

Example diagnostic shape:

```text
health:
  mode: relative_health_v2
  condition_state: ok
  calculation_state: ready
  trend_state: stable
  confidence: 0.72
  assessment_mode: relative_voltage
  signal: voltage_mv
  metrics:
    current_24h_robust: 2891
    reference_7d: 2906
    reference_30d: 2914
    reference_used: 2914
    ratio_to_reference: 0.992
    delta_from_reference: -23
  reasons:
    - relative_voltage_stable
```

The disabled deep diagnostic sensor exposes canonical `health_summary`,
`health_states`, `health_calculation`, `health_trends`, `health_modes`,
`health_thresholds` and one full `health` diagnostic object per device. Temporary
`health_shadow`, `health_v2_shadow`, `legacy_health_*`, `shadow_health_*` and
`health_v2_*` parity aliases are removed in dev27.

A null v2 condition is allowed only when neither current battery percentage nor
voltage provides a usable condition signal. That case is reported separately as
`calculation_state: unavailable`; it is not a battery-condition category.

## Temperature source selection

Temperature context can block voltage-baseline learning when a strong
voltage-to-temperature relationship is observed, so source identity is
safety-relevant.

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
decides how telemetry may be used; it does not itself score battery health. It
determines:

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
least three cadence gaps are available, current age is compared with the learned
device-specific p90 gap.

From dev19 the cadence Store keeps at most one representative timestamp per fixed
15-minute UTC bucket over a seven-day retention window. The 768-point hard cap
leaves headroom above the approximately 672 buckets needed for seven complete
days. Existing dev13-dev18 `timestamps` data is loaded through the same schema and
compacted automatically.

`reports_24h` means retained **time-balanced cadence points**, not physical Zigbee
or MQTT packet count. It is diagnostic only and is not a health weight. Live
`last_seen` changes immediately refresh freshness and the derived
evidence/cycle/baseline/health assessment, but do not write the baseline-v2 Store
or launch a full Recorder/profiler cycle.

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
Its records remain `provisional`; legacy learning stays `shadow_no_save` and must
not feed the production health engine. Dev20 does not migrate, overwrite or delete
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
14. Prefer measured temperature over diagnostic device temperature. ✅ dev22
15. Publish coordinator-backed per-device and aggregate health entities. ✅ dev23
16. Replace calculable `unknown` with relative condition + trend + calculation quality. 🧪 dev24
17. Promote accepted Health Model v2 to the production entity contract. ⏳
18. Release hardening / v1 candidate. ⏳

Daily profiler aggregates are intentionally not persisted yet. Cadence samples
and guarded baseline-v2 records are persisted because they represent learned
state that cannot be reconstructed reliably from one current Recorder window.
Health state itself is derived and is therefore not stored separately.

## Verification

Local commands:

```bash
python -m unittest discover -s tests -v
python -m compileall custom_components tests
ruff check custom_components tests
```

Dev24 adds a GitHub Actions CI workflow using the Home Assistant 2026.9 compatibility
line so compile, Ruff and unit tests can be physically executed on each push and
pull request. Real Home Assistant output remains the acceptance gate for the new
relative-health semantics because synthetic tests cannot establish correct
calibration on the actual Zigbee2MQTT device population.

## Compatibility target

Home Assistant 2026.9 or newer.
