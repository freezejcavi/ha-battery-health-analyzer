# Battery Health Analyzer

Battery Health Analyzer is a Home Assistant custom integration that evaluates battery
condition from historical telemetry instead of trusting one instantaneous percentage.

The integration is intentionally limited to battery devices provided by Home Assistant's
**MQTT integration**. The primary validation environment is **Zigbee2MQTT**.

## Release status

**Current release candidate: `0.1.0-rc.2`**

RC2 keeps the accepted Health Model v2 thresholds, Store schema, MQTT scope and
freshness/cycle semantics, while adding a cross-signal **Telemetry Integrity** layer.
That layer detects contradictory or implausible runtime telemetry before it can contaminate
baseline learning or produce a misleading battery-condition verdict.

The pre-RC contract was validated on a real Home Assistant installation with 31 MQTT
battery devices. The accepted snapshot contained:

- 25 `ok`
- 5 `declining`
- 1 `weakening`
- 0 `replace`
- 0 unavailable
- Summary `weakening`
- 31/31 devices `fresh`
- 31/31 Evidence Routing `ready`
- 31/31 current Cycle Integrity `stable`
- 7 retained guarded baseline-v2 records

These counts describe the validation installation; they are not expected values for other
homes.

## Scope

Discovery starts from Home Assistant Entity Registry entries whose `platform` is exactly
`mqtt`. The integration deliberately does not discover battery entities owned by
`mobile_app`, Hue, ZHA, ESPHome or vendor-specific integrations.

For each supported MQTT device it can use:

- battery percentage;
- battery voltage;
- `last_seen`;
- `power_outage_count` when available;
- one safe same-device temperature source when available.

This MQTT-only boundary is intentional for the first release line.

## Published entities

Each discovered MQTT battery device receives one enum health sensor with four production
states:

- `ok`
- `declining`
- `weakening`
- `replace`

`unknown` is not a battery-condition state. If the current condition genuinely cannot be
calculated from trustworthy telemetry, the entity becomes Home Assistant `unavailable`.

Entity IDs are source-derived where possible:

```text
sensor.dvere_balkon_battery -> sensor.dvere_balkon_health
```

The stable unique ID remains based on the config entry plus the Home Assistant `device_id`.
A one-time migration renames only old system-generated Battery Health Analyzer entity IDs;
explicitly user-customized IDs are preserved.

The aggregate sensor is:

```text
sensor.battery_health_summary
```

Summary uses actionable precedence:

```text
replace > weakening > declining > ok
```

## Public health attributes

Per-device health entities expose a deliberately small user-facing contract:

- `trend`
- `confidence_percent`
- `calculation`
- `basis`
- `current`
- `reference`
- `unit`
- `change_from_reference`
- `change_unit`
- `reason`
- optional `note` when calculation quality is limited

Internal source IDs, cycle generation, raw reason codes and intermediate references are
kept out of the normal health entity.

## How Health Model v2 works

The model evaluates the **best available condition signal relative to the device's own
history**. Instantaneous battery percentages are context, not the primary verdict input.

For informative continuous voltage, the model uses robust historical references including:

- current 24-hour p90;
- recent 7-day upper reference;
- robust 30-day upper reference;
- recent direction from multi-day blocks;
- a guarded persisted baseline when it is valid for the current battery cycle.

If voltage is missing, static, quantized or otherwise unsuitable as the primary condition
signal, battery percentage uses the same relative-history principle.

The condition states mean:

- `ok`: current robust regime remains close to its own reference;
- `declining`: early persistent deterioration relative to own history;
- `weakening`: material persistent deterioration;
- `replace`: deep persistent deterioration **or a telemetry-integrity condition that
  requires physical battery/service inspection**.

Calculation quality is separate from battery condition:

- `ready`: normal evidence path;
- `limited`: condition is calculable but one or more safety/context limitations reduce
  confidence or escalation freedom;
- `unavailable`: there is no sufficiently trustworthy current condition signal.

Trend is also independent of condition and can be `stable`, `falling`, `recovering`,
`volatile` or `insufficient`.

## Safety layers

### Telemetry Integrity

Telemetry Integrity runs before the battery-condition model. It cross-checks available
battery, voltage, diagnostic temperature and power-outage evidence and classifies the
runtime input as:

- `trusted`: no material contradiction detected;
- `guarded`: suspicious telemetry exists, so the condition remains conservative and
  calculation quality is limited;
- `service_required`: multiple severe or corroborating anomalies require a physical
  battery/device inspection.

A service-required result publishes `replace` with a telemetry-integrity basis. In this
path, `replace` means **inspect the device, cells and contacts**; it does not claim that
every installed cell is proven exhausted. Individual cells can therefore be measured and
only the weak or failed cell(s) replaced when appropriate.

Guarded and service-required telemetry cannot create or update the persisted baseline-v2
record, so one corrupted report cannot teach the model a bad reference.

### Evidence Routing

Evidence Routing decides how each telemetry channel may be used. It prevents strongly
coupled battery percentage and voltage from being counted as two independent signals and
classifies voltage information as continuous, quantized, static or insufficient.

### Freshness

`last_seen` is an operability/freshness gate, not proof of a healthy battery. Report cadence
is learned prospectively from live Home Assistant state changes and stored in a compact,
time-balanced seven-day Store.

There is no universal fixed one- or two-hour stale threshold. Current age is compared with
the learned device-specific cadence.

### Cycle Integrity

Cycle Integrity detects conservative upward regime shifts that may indicate a battery
replacement. A new cycle is not confirmed from one correlated signal or a transient jump.
Possible boundaries quarantine baseline learning rather than forcing a new battery cycle.

### Guarded baseline-v2 Store

Only an eligible baseline assessment may create or update a persisted baseline-v2 record.
Within the same cycle the stored baseline never learns downward. A new cycle generation is
created only after a confirmed segmented boundary and enough clean post-boundary evidence.

## Temperature handling

Temperature can materially affect voltage. When both regular measured `temperature` and
Zigbee2MQTT diagnostic `device_temperature` exist on the same device, the measured
`temperature` source is preferred. Diagnostic device temperature is only a fallback when it
is the sole safe candidate.

Temperature-sensitive voltage paths may remain calculable but are marked `limited`, or the
model may bypass voltage and use battery percentage when that is safer.

Full temperature normalization is not part of RC2.

## Deep diagnostics

The diagnostic entity is:

```text
sensor.battery_health_analyzer_discovered_devices
```

It is `EntityCategory.DIAGNOSTIC` and is disabled by default for new registry entries. The
deep payload contains canonical production diagnostics such as:

- `health_summary`
- `health_states`
- `health_calculation`
- `health_trends`
- `health_modes`
- `health_thresholds`
- full per-device discovery, telemetry, evidence, cycle and baseline diagnostics

Temporary legacy/shadow parity aliases were removed before RC1 and remain absent in RC2.

## Persistence

Battery Health Analyzer persists only learned state that cannot be safely reconstructed from
a single current Recorder window:

- time-balanced `last_seen` cadence samples;
- guarded baseline-v2 / cycle state.

The health verdict itself is derived and is not stored separately.

The older dev7/dev8 baseline Store remains read-only and provisional for diagnostics. It does
not feed the production Health Model v2.

## Known limitations of RC2

- MQTT integration only; Zigbee2MQTT is the primary validated environment.
- Thresholds are conservative calibration hypotheses, not chemistry-specific battery models.
- The project does not yet have a broad ground-truth dataset covering many real battery
  replacement events and chemistries.
- Full temperature normalization is not implemented.
- There is no remaining-days prediction.
- Daily profiler aggregates are reconstructed from Recorder rather than persisted.

These limits are intentionally explicit so the integration prefers reduced confidence over
false precision.

## Installation

The repository is HACS-ready as a custom integration.

1. Add this repository to HACS as a custom **Integration** repository.
2. Install Battery Health Analyzer.
3. Restart Home Assistant when HACS requests it.
4. Add **Battery Health Analyzer** from **Settings -> Devices & services**.

The integration is `single_config_entry` and requires Home Assistant Recorder.

## Verification

The repository CI runs on every push and pull request and includes:

```bash
python -m compileall -q custom_components tests
ruff check custom_components/battery_health tests
python -m unittest discover -s tests -v
```

Separate validation jobs run Home Assistant Hassfest and HACS validation.

The RC2 development head passed repository compile, full Ruff, the complete unit-test suite,
Hassfest and HACS validation before release preparation.

## Compatibility target

Home Assistant **2026.9 or newer**.
