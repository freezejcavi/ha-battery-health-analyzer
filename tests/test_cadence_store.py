"""Tests for time-balanced live cadence retention."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.battery_health.cadence_store import (
    CADENCE_MAX_SAMPLES,
    CADENCE_SAMPLE_INTERVAL_MINUTES,
    CadenceStore,
)
from custom_components.battery_health.operability import summarize_freshness


class CadenceStoreTests(unittest.TestCase):
    """Verify high-rate devices cannot collapse the seven-day horizon."""

    def setUp(self) -> None:
        self.end = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)

    def test_keeps_latest_point_per_fixed_bucket(self) -> None:
        points = [
            self.end - timedelta(minutes=31),
            self.end - timedelta(minutes=25),
            self.end - timedelta(minutes=16),
        ]
        result = CadenceStore._sanitize(points, self.end)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], self.end - timedelta(minutes=31))
        self.assertEqual(result[1], self.end - timedelta(minutes=16))

    def test_chatty_seven_day_history_fits_without_collapsing_horizon(self) -> None:
        points = [
            self.end - timedelta(minutes=minute)
            for minute in range(0, 7 * 24 * 60 + 1, 5)
        ]
        result = CadenceStore._sanitize(points, self.end)

        self.assertLessEqual(len(result), CADENCE_MAX_SAMPLES)
        self.assertGreaterEqual(len(result), 7 * 24 * 4 - 2)
        self.assertLessEqual(result[0], self.end - timedelta(days=6, hours=23))

    def test_sparse_device_is_not_artificially_resampled(self) -> None:
        points = [self.end - timedelta(hours=hours) for hours in (150, 114, 78, 42, 6)]
        result = CadenceStore._sanitize(points, self.end)

        self.assertEqual(result, sorted(points))

    def test_chatty_device_uses_time_balanced_cadence_for_freshness(self) -> None:
        raw = [self.end - timedelta(minutes=minute) for minute in range(1, 181)]
        sampled = CadenceStore._sanitize(raw, self.end)
        result = summarize_freshness(
            sampled,
            self.end - timedelta(minutes=5),
            self.end,
            self.end - timedelta(days=7),
        )

        self.assertEqual(CADENCE_SAMPLE_INTERVAL_MINUTES, 15)
        self.assertGreaterEqual(result.p90_gap_seconds or 0, 10 * 60)
        self.assertEqual(result.state, "fresh")


if __name__ == "__main__":
    unittest.main()
