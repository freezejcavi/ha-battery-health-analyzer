"""Tests for sparse live-learned freshness cadence."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.battery_health.operability import summarize_freshness


class LiveCadenceTests(unittest.TestCase):
    """Verify that longer learned cadence remains device-specific."""

    def test_sparse_device_can_learn_from_seven_day_window(self) -> None:
        observed_at = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        cadence_start = observed_at - timedelta(days=7)
        reports = [
            observed_at - timedelta(hours=hours) for hours in (150, 114, 78, 42, 6)
        ]

        result = summarize_freshness(
            reports,
            observed_at - timedelta(hours=6),
            observed_at,
            cadence_start,
        )

        self.assertEqual(result.cadence_samples, 4)
        self.assertAlmostEqual(result.p90_gap_seconds, 36 * 3600)
        self.assertEqual(result.state, "fresh")

    def test_learned_sparse_cadence_can_still_become_stale(self) -> None:
        observed_at = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
        cadence_start = observed_at - timedelta(days=7)
        reports = [
            observed_at - timedelta(hours=hours) for hours in (150, 126, 102, 78, 54)
        ]

        result = summarize_freshness(
            reports,
            observed_at - timedelta(hours=54),
            observed_at,
            cadence_start,
        )

        self.assertEqual(result.cadence_samples, 4)
        self.assertAlmostEqual(result.p90_gap_seconds, 24 * 3600)
        self.assertEqual(result.state, "late")


if __name__ == "__main__":
    unittest.main()
