"""Update coordinator for Battery Health Analyzer."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .baseline import learn_baseline
from .baseline_v2 import BaselineV2Assessment, assess_guarded_baseline_v2
from .baseline_v2_store import (
    BaselineV2PersistenceResult,
    BaselineV2Record,
    BaselineV2Store,
)
from .cadence_store import CadenceStore
from .const import ANALYSIS_INTERVAL, DOMAIN, HISTORY_WINDOW
from .cycle import CycleIntegrity, assess_cycle_integrity
from .evidence import (
    EvidenceModel,
    build_evidence_model,
    classify_voltage_information,
)
from .ha_discovery import async_discover_battery_devices
from .health import ShadowHealthAssessment, assess_shadow_health
from .health_v2 import RelativeHealthAssessment, assess_relative_health_v2
from .models import (
    BaselineLearningResult,
    BatteryHealthSnapshot,
    DiscoveredBatteryDevice,
    LongTermHistorySnapshot,
    TelemetryProfile,
)
from .operability import (
    OperabilitySnapshot,
    parse_last_seen,
    summarize_freshness,
)
from .operability_recorder import async_get_operability_history
from .profiler import build_telemetry_profile
from .recorder import (
    async_get_long_term_history,
    async_get_recorder_history,
)
from .storage import BaselineStore

_LOGGER = logging.getLogger(__name__)


class BatteryHealthCoordinator(DataUpdateCoordinator[BatteryHealthSnapshot]):
    """Coordinate telemetry analysis, health assessment and guarded persistence."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=ANALYSIS_INTERVAL,
        )
        self._baseline_store = BaselineStore(hass)
        self._baseline_v2_store = BaselineV2Store(hass)
        self._cadence_store = CadenceStore(hass)
        self._long_term_history: LongTermHistorySnapshot | None = None
        self._last_seen_unsub = None
        self._last_seen_entity_to_device: dict[str, str] = {}
        self.operability = OperabilitySnapshot({}, {})
        self.evidence_models: dict[str, EvidenceModel] = {}
        self.cycle_integrity: dict[str, CycleIntegrity] = {}
        self.baseline_v2_assessments: dict[str, BaselineV2Assessment] = {}
        self.baseline_v2_persistence: dict[str, BaselineV2PersistenceResult] = {}
        self.health_assessments: dict[str, ShadowHealthAssessment] = {}
        self.health_v2_assessments: dict[str, RelativeHealthAssessment] = {}

    @property
    def baseline_v2_records(self) -> dict[str, BaselineV2Record]:
        """Expose the current guarded Store snapshot for diagnostics."""
        return self._baseline_v2_store.records

    async def async_initialize(self) -> None:
        """Load existing persistent state before the first refresh."""
        await self._baseline_store.async_load()
        await self._baseline_v2_store.async_load()
        await self._cadence_store.async_load()

    def _evaluate_device_decision(
        self,
        device: DiscoveredBatteryDevice,
        snapshot: BatteryHealthSnapshot,
        profile: TelemetryProfile,
        observed_at,
        *,
        persist: bool,
    ) -> BaselineV2PersistenceResult | None:
        """Evaluate evidence, cycle, baseline and both health models for one device."""
        long_term = self._long_term_history
        if long_term is None:
            return None

        battery_daily = (
            long_term.battery_daily.get(device.battery_entity_id, {})
            if device.battery_entity_id is not None
            else {}
        )
        voltage_daily = (
            long_term.voltage_daily.get(device.voltage_entity_id, {})
            if device.voltage_entity_id is not None
            else {}
        )
        battery_history = (
            snapshot.battery_history.get(device.battery_entity_id)
            if device.battery_entity_id is not None
            else None
        )
        voltage_history = (
            snapshot.voltage_history.get(device.voltage_entity_id)
            if device.voltage_entity_id is not None
            else None
        )
        freshness = (
            self.operability.freshness.get(device.last_seen_entity_id)
            if device.last_seen_entity_id is not None
            else None
        )
        outage = (
            self.operability.outages.get(device.outage_entity_id)
            if device.outage_entity_id is not None
            else None
        )
        voltage_information = classify_voltage_information(voltage_daily)
        evidence_model = build_evidence_model(
            profile,
            battery_history,
            voltage_history,
            freshness,
            outage,
            voltage_information,
        )
        cycle_integrity = assess_cycle_integrity(
            battery_daily,
            voltage_daily,
            battery_history,
            voltage_history,
            voltage_information,
            freshness.state if freshness is not None else None,
            evidence_model.battery_voltage_topology,
        )
        assessment = assess_guarded_baseline_v2(
            battery_daily,
            voltage_daily,
            voltage_history,
            evidence_model,
            cycle_integrity,
            voltage_information,
        )

        self.evidence_models[device.device_id] = evidence_model
        self.cycle_integrity[device.device_id] = cycle_integrity
        self.baseline_v2_assessments[device.device_id] = assessment

        persistence: BaselineV2PersistenceResult | None = None
        if persist:
            persistence = self._baseline_v2_store.apply(
                device.device_id,
                assessment,
                observed_at,
            )
            self.baseline_v2_persistence[device.device_id] = persistence

        persisted_record = self._baseline_v2_store.records.get(device.device_id)
        persisted_mv = (
            persisted_record.baseline_mv if persisted_record is not None else None
        )
        persisted_confidence = (
            persisted_record.confidence if persisted_record is not None else None
        )

        # Dev23 production classifier remains unchanged during dev24 validation.
        self.health_assessments[device.device_id] = assess_shadow_health(
            battery_history,
            voltage_history,
            profile,
            evidence_model,
            cycle_integrity,
            assessment,
            persisted_baseline_mv=persisted_mv,
            persisted_baseline_confidence=persisted_confidence,
        )

        # Dev24 Health Model v2 is a parallel, read-only shadow model. It consumes
        # the same current and long-term data but treats condition, trend and
        # calculation quality as separate outputs.
        self.health_v2_assessments[device.device_id] = assess_relative_health_v2(
            battery_history,
            voltage_history,
            battery_daily,
            voltage_daily,
            evidence_model,
            cycle_integrity,
            assessment,
            persisted_baseline_mv=persisted_mv,
            persisted_baseline_confidence=persisted_confidence,
        )
        return persistence

    @callback
    def _publish_live_freshness(
        self,
        entity_id: str,
        device_id: str,
        timestamp,
        observed_at,
    ) -> None:
        """Publish one live freshness update without running Recorder analysis."""
        learned = self._cadence_store.timestamps(device_id, observed_at)
        evidence = summarize_freshness(
            learned,
            timestamp,
            observed_at,
            observed_at - HISTORY_WINDOW,
            source="current",
        )
        freshness = dict(self.operability.freshness)
        freshness[entity_id] = evidence
        self.operability = OperabilitySnapshot(
            freshness=freshness,
            outages=self.operability.outages,
        )

        snapshot = self.data
        if snapshot is not None:
            device = next(
                (item for item in snapshot.devices if item.device_id == device_id),
                None,
            )
            profile = snapshot.telemetry_profiles.get(device_id)
            if device is not None and profile is not None:
                self._evaluate_device_decision(
                    device,
                    snapshot,
                    profile,
                    observed_at,
                    persist=False,
                )

        self.async_update_listeners()

    @callback
    def _async_handle_last_seen_change(
        self,
        event: Event[EventStateChangedData],
    ) -> None:
        """Learn and publish one live last_seen sample without touching Recorder."""
        entity_id = event.data["entity_id"]
        device_id = self._last_seen_entity_to_device.get(entity_id)
        new_state = event.data["new_state"]
        if device_id is None or new_state is None:
            return
        timestamp = parse_last_seen(new_state.state)
        if timestamp is None:
            return
        observed_at = dt_util.utcnow()
        self._cadence_store.observe(device_id, timestamp, observed_at)
        self._publish_live_freshness(
            entity_id,
            device_id,
            timestamp,
            observed_at,
        )

    @callback
    def _ensure_last_seen_tracking(self, devices, observed_at) -> None:
        """Track the current discovered last_seen entity set."""
        mapping = {
            device.last_seen_entity_id: device.device_id
            for device in devices
            if device.last_seen_entity_id is not None
        }
        if mapping == self._last_seen_entity_to_device:
            return

        if self._last_seen_unsub is not None:
            self._last_seen_unsub()
            self._last_seen_unsub = None

        self._last_seen_entity_to_device = mapping
        if mapping:
            self._last_seen_unsub = async_track_state_change_event(
                self.hass,
                list(mapping),
                self._async_handle_last_seen_change,
            )

        # Seed with any valid live values already available at registration time.
        for entity_id, device_id in mapping.items():
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            timestamp = parse_last_seen(state.state)
            if timestamp is not None:
                self._cadence_store.observe(device_id, timestamp, observed_at)

    async def async_shutdown(self) -> None:
        """Stop live tracking and flush compact persistent state."""
        if self._last_seen_unsub is not None:
            self._last_seen_unsub()
            self._last_seen_unsub = None
        await self._cadence_store.async_save()
        await self._baseline_v2_store.async_save()
        await super().async_shutdown()

    async def _async_update_data(self) -> BatteryHealthSnapshot:
        """Return one telemetry snapshot and apply guarded v2 Store transitions."""
        devices = tuple(async_discover_battery_devices(self.hass))
        voltage_entity_ids = sorted(
            device.voltage_entity_id
            for device in devices
            if device.voltage_entity_id is not None
        )
        battery_entity_ids = sorted(
            device.battery_entity_id
            for device in devices
            if device.battery_entity_id is not None
        )
        temperature_entity_ids = sorted(
            device.temperature_entity_id
            for device in devices
            if device.temperature_entity_id is not None
        )
        last_seen_entity_ids = sorted(
            device.last_seen_entity_id
            for device in devices
            if device.last_seen_entity_id is not None
        )
        outage_entity_ids = sorted(
            device.outage_entity_id
            for device in devices
            if device.outage_entity_id is not None
        )
        observed_at = dt_util.utcnow()
        self._ensure_last_seen_tracking(devices, observed_at)

        cadence_timestamps = {
            device.last_seen_entity_id: self._cadence_store.timestamps(
                device.device_id,
                observed_at,
            )
            for device in devices
            if device.last_seen_entity_id is not None
        }

        recorder_history = await async_get_recorder_history(
            self.hass,
            voltage_entity_ids,
            battery_entity_ids,
            observed_at,
        )
        self.operability = await async_get_operability_history(
            self.hass,
            last_seen_entity_ids,
            outage_entity_ids,
            observed_at,
            cadence_timestamps=cadence_timestamps,
        )
        if self._long_term_history is None:
            self._long_term_history = await async_get_long_term_history(
                self.hass,
                voltage_entity_ids,
                battery_entity_ids,
                temperature_entity_ids,
                observed_at,
            )

        voltage_history = recorder_history.voltage_history
        battery_percent: dict[str, float | None] = {}
        battery_percent_source: dict[str, str] = {}
        baseline_learning: dict[str, BaselineLearningResult] = {}
        telemetry_profiles: dict[str, TelemetryProfile] = {}

        for device in devices:
            percentage = (
                recorder_history.battery_percent.get(device.battery_entity_id)
                if device.battery_entity_id is not None
                else None
            )
            battery_percent[device.device_id] = percentage
            battery_percent_source[device.device_id] = (
                recorder_history.battery_percent_source.get(
                    device.battery_entity_id,
                    "unavailable",
                )
                if device.battery_entity_id is not None
                else "unavailable"
            )

            history_summary = (
                voltage_history.get(device.voltage_entity_id)
                if device.voltage_entity_id is not None
                else None
            )
            baseline_learning[device.device_id] = learn_baseline(
                self._baseline_store.records.get(device.device_id),
                history_summary,
                percentage,
                observed_at,
            )

            battery_daily = (
                self._long_term_history.battery_daily.get(
                    device.battery_entity_id,
                    {},
                )
                if device.battery_entity_id is not None
                else {}
            )
            voltage_daily = (
                self._long_term_history.voltage_daily.get(
                    device.voltage_entity_id,
                    {},
                )
                if device.voltage_entity_id is not None
                else {}
            )
            temperature_daily = (
                self._long_term_history.temperature_daily.get(
                    device.temperature_entity_id,
                    {},
                )
                if device.temperature_entity_id is not None
                else {}
            )
            telemetry_profiles[device.device_id] = build_telemetry_profile(
                battery_daily,
                voltage_daily,
                temperature_daily,
            )

        snapshot = BatteryHealthSnapshot(
            devices=devices,
            voltage_history=voltage_history,
            battery_percent=battery_percent,
            battery_percent_source=battery_percent_source,
            baseline_learning=baseline_learning,
            battery_history=recorder_history.battery_history,
            telemetry_profiles=telemetry_profiles,
        )

        self.evidence_models = {}
        self.cycle_integrity = {}
        self.baseline_v2_assessments = {}
        self.baseline_v2_persistence = {}
        self.health_assessments = {}
        self.health_v2_assessments = {}
        for device in devices:
            profile = telemetry_profiles.get(device.device_id)
            if profile is None:
                continue
            self._evaluate_device_decision(
                device,
                snapshot,
                profile,
                observed_at,
                persist=True,
            )

        if self._baseline_v2_store.dirty:
            await self._baseline_v2_store.async_save()

        return snapshot
