"""Tests for guarded baseline v2 persistence planning."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime

from custom_components.battery_health.baseline_v2 import (
    BaselineV2Assessment,
    CycleSegment,
)
from custom_components.battery_health.baseline_v2_store import (
    BaselineV2Record,
    BaselineV2Store,
    plan_baseline_v2_persistence,
)


NOW = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)


def assessment(
    *,
    eligibility: str = "eligible",
    candidate: float | None = 3050,
    confidence: float = 0.65,
    state: str = "left_censored",
    boundary_date: str | None = None,
    anchor: str = "battery_upper_bootstrap",
) -> BaselineV2Assessment:
    return BaselineV2Assessment(
        eligibility=eligibility,
        confidence=confidence,
        candidate_mv=candidate,
        candidate_source="cycle_segment_upper_envelope" if candidate is not None else None,
        anchor=anchor,
        voltage_days=30 if candidate is not None else 0,
        coverage=0.99 if candidate is not None else None,
        cycle_segment=CycleSegment(
            state=state,
            boundary_kind="probable_boundary" if state == "segmented" else None,
            boundary_date=boundary_date,
            cycle_start_known=False,
            usable_days=("2026-09-13", "2026-09-14") if candidate is not None else (),
            excluded_days=0,
            reasons=(),
        ),
        limitations=(),
    )


def record(
    *,
    baseline: float = 3050,
    confidence: float = 0.65,
    generation: int = 1,
    boundary_date: str | None = None,
) -> BaselineV2Record:
    return BaselineV2Record(
        baseline_mv=baseline,
        confidence=confidence,
        cycle_generation=generation,
        boundary_date=boundary_date,
        cycle_start_known=False,
        anchor="battery_upper_bootstrap",
        source="cycle_segment_upper_envelope",
        created_at=NOW,
        updated_at=NOW,
    )


class FailingThenSuccessfulStore:
    """Minimal async Store fake for retry semantics."""

    def __init__(self) -> None:
        self.calls = 0

    async def async_save(self, data) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated transient write failure")


class BaselineV2PersistenceTests(unittest.TestCase):
    """Verify guarded Store transitions cannot learn unsafe state."""

    def test_eligible_left_censored_bootstrap_creates_generation_one(self) -> None:
        result = plan_baseline_v2_persistence(None, assessment(), NOW)

        self.assertTrue(result.changed)
        self.assertEqual(result.state, "created")
        self.assertIsNotNone(result.record)
        assert result.record is not None
        self.assertEqual(result.record.baseline_mv, 3050)
        self.assertEqual(result.record.cycle_generation, 1)
        self.assertIsNone(result.record.boundary_date)

    def test_noneligible_assessment_never_rewrites_existing_record(self) -> None:
        existing = record()
        result = plan_baseline_v2_persistence(
            existing,
            assessment(eligibility="blocked", candidate=None, confidence=0),
            NOW,
        )

        self.assertFalse(result.changed)
        self.assertEqual(result.state, "retained_guarded")
        self.assertIs(result.record, existing)

    def test_same_cycle_candidate_never_learns_downward(self) -> None:
        existing = record(baseline=3050)
        result = plan_baseline_v2_persistence(
            existing,
            assessment(candidate=3000),
            NOW,
        )

        self.assertFalse(result.changed)
        self.assertEqual(result.state, "retained")
        self.assertEqual(result.record.baseline_mv, 3050)

    def test_same_cycle_requires_material_raise(self) -> None:
        existing = record(baseline=3000)
        small = plan_baseline_v2_persistence(
            existing,
            assessment(candidate=3020),
            NOW,
        )
        raised = plan_baseline_v2_persistence(
            existing,
            assessment(candidate=3040),
            NOW,
        )

        self.assertFalse(small.changed)
        self.assertEqual(small.record.baseline_mv, 3000)
        self.assertTrue(raised.changed)
        self.assertEqual(raised.state, "raised")
        self.assertEqual(raised.record.baseline_mv, 3040)

    def test_new_confirmed_boundary_starts_exactly_one_new_generation(self) -> None:
        existing = record(baseline=3050, generation=1)
        first = plan_baseline_v2_persistence(
            existing,
            assessment(
                candidate=3200,
                confidence=1.0,
                state="segmented",
                boundary_date="2026-09-08",
                anchor="observed_cycle_boundary",
            ),
            NOW,
        )
        self.assertTrue(first.changed)
        self.assertEqual(first.state, "cycle_started")
        assert first.record is not None
        self.assertEqual(first.record.cycle_generation, 2)
        self.assertEqual(first.record.boundary_date, "2026-09-08")
        self.assertEqual(first.record.baseline_mv, 3200)

        second = plan_baseline_v2_persistence(
            first.record,
            assessment(
                candidate=3200,
                confidence=1.0,
                state="segmented",
                boundary_date="2026-09-08",
                anchor="observed_cycle_boundary",
            ),
            NOW,
        )
        self.assertFalse(second.changed)
        self.assertEqual(second.state, "retained")
        self.assertEqual(second.record.cycle_generation, 2)

    def test_storage_roundtrip_preserves_cycle_identity(self) -> None:
        original = record(
            baseline=3200,
            confidence=1.0,
            generation=3,
            boundary_date="2026-09-08",
        )
        restored = BaselineV2Record.from_storage_dict(original.as_storage_dict())
        self.assertEqual(restored, original)

    def test_dirty_survives_failed_save_until_retry_succeeds(self) -> None:
        store = object.__new__(BaselineV2Store)
        fake_store = FailingThenSuccessfulStore()
        store._store = fake_store
        store.records = {}
        store._dirty = False

        result = store.apply("device-1", assessment(), NOW)
        self.assertTrue(result.changed)
        self.assertTrue(store.dirty)

        with self.assertRaises(RuntimeError):
            asyncio.run(store.async_save())
        self.assertTrue(store.dirty)

        asyncio.run(store.async_save())
        self.assertFalse(store.dirty)
        self.assertEqual(fake_store.calls, 2)


if __name__ == "__main__":
    unittest.main()
