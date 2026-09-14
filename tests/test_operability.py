"""Tests for freshness and outage evidence."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from custom_components.battery_health.operability import (
    ISSUE_FUTURE_LAST_SEEN,
    ISSUE_INSUFFICIENT_CADENCE,
    ISSUE_PARTIAL_OUTAGE_WINDOW,
    OutageHistoryPoint,
    parse_last_seen,
    parse_outage_count,
    summarize_freshness,
    summarize_outage_history,
)


class OperabilityTests(unittest.TestCase):
    """Verify conservative freshness and outage calculations."""

    def setUp(self) -> None:
        self.end = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
        self.start = self.end - timedelta(hours=24)

    def test_parse_values(self) -> None:
        self.assertEqual(
            parse_last_seen("2026-09-14T09:58:00Z"),
            datetime(2026, 9, 14, 9, 58, tzinfo=UTC),
        )
        self.assertEqual(parse_outage_count("14"), 14)
        self.assertIsNone(parse_outage_count("14.5"))

    def test_freshness_uses_device_specific_cadence(self) -> None:
        reports = [
            self.end - timedelta(hours=value)
            for value in (5, 4, 3, 2, 1)
        ]
        result = summarize_freshness(
            reports,
            self.end - timedelta(minutes=20),
            self.end,
            self.start,
        )
        self.assertEqual(result.state, "fresh")
        self.assertEqual(result.reports_24h, 6)
        self.assertAlmostEqual(result.p90_gap_seconds, 3600)

    def test_freshness_late_and_stale(self) -> None:
        reports = [
            self.end - timedelta(hours=value)
            for value in range(12, 2, -1)
        ]
        late = summarize_freshness(
            reports,
            self.end - timedelta(hours=2),
            self.end,
            self.start,
        )
        stale = summarize_freshness(
            reports,
            self.end - timedelta(hours=4),
            self.end,
            self.start,
        )
        self.assertEqual(late.state, "late")
        self.assertEqual(stale.state, "stale")

    def test_freshness_insufficient_and_future(self) -> None:
        insufficient = summarize_freshness(
            [self.end - timedelta(hours=2)],
            self.end - timedelta(hours=1),
            self.end,
            self.start,
        )
        self.assertEqual(insufficient.issue, ISSUE_INSUFFICIENT_CADENCE)
        future = summarize_freshness(
            [],
            self.end + timedelta(minutes=10),
            self.end,
            self.start,
        )
        self.assertEqual(future.issue, ISSUE_FUTURE_LAST_SEEN)

    def test_outage_positive_delta_and_reset(self) -> None:
        points = [
            OutageHistoryPoint(self.start - timedelta(minutes=1), 14),
            OutageHistoryPoint(self.start + timedelta(hours=4), 15),
            OutageHistoryPoint(self.start + timedelta(hours=8), 17),
            OutageHistoryPoint(self.start + timedelta(hours=12), 0),
            OutageHistoryPoint(self.start + timedelta(hours=13), 1),
        ]
        result = summarize_outage_history(points, self.start, self.end)
        self.assertEqual(result.events_24h, 4)
        self.assertEqual(result.increment_transitions_24h, 3)
        self.assertEqual(result.resets_24h, 1)

    def test_outage_partial_and_unsupported(self) -> None:
        partial = summarize_outage_history(
            [
                OutageHistoryPoint(self.start + timedelta(hours=2), 14),
                OutageHistoryPoint(self.start + timedelta(hours=3), 15),
            ],
            self.start,
            self.end,
        )
        self.assertEqual(partial.issue, ISSUE_PARTIAL_OUTAGE_WINDOW)
        unsupported = summarize_outage_history(
            [],
            self.start,
            self.end,
            supported=False,
        )
        self.assertFalse(unsupported.supported)
        self.assertIsNone(unsupported.events_24h)


if __name__ == "__main__":
    unittest.main()
