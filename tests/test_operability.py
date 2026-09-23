"""Tests for freshness and outage evidence."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from custom_components.battery_health.operability import (
    ISSUE_FUTURE_LAST_SEEN,
    ISSUE_INSUFFICIENT_CADENCE,
    ISSUE_NO_OUTAGE_HISTORY,
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

    def test_parse_last_seen_formats(self) -> None:
        self.assertEqual(
            parse_last_seen("2026-09-14T09:58:00Z"),
            datetime(2026, 9, 14, 9, 58, tzinfo=UTC),
        )
        local = parse_last_seen("2026-09-14T11:58:00+02:00")
        self.assertEqual(local, datetime(2026, 9, 14, 9, 58, tzinfo=UTC))
        epoch = parse_last_seen("1789379880000")
        self.assertIsNotNone(epoch)
        self.assertEqual(epoch.tzinfo, UTC)

    def test_parse_outage_count(self) -> None:
        self.assertEqual(parse_outage_count("14"), 14)
        self.assertEqual(parse_outage_count(14.0), 14)
        self.assertIsNone(parse_outage_count("14.5"))
        self.assertIsNone(parse_outage_count("unavailable"))
        self.assertIsNone(parse_outage_count(-1))

    def test_freshness_uses_device_specific_cadence(self) -> None:
        reports = [self.end - timedelta(hours=value) for value in (5, 4, 3, 2, 1)]
        result = summarize_freshness(
            reports,
            self.end - timedelta(minutes=20),
            self.end,
            self.start,
        )
        self.assertEqual(result.state, "fresh")
        self.assertEqual(result.reports_24h, 6)
        self.assertEqual(result.cadence_samples, 5)
        self.assertAlmostEqual(result.p90_gap_seconds, 3600)
        self.assertLess(result.age_to_p90_ratio, 1)

    def test_freshness_uses_older_cadence_but_reports_24h_is_bounded(self) -> None:
        reports = [self.end - timedelta(hours=value) for value in (96, 72, 48, 24)]
        result = summarize_freshness(
            reports,
            self.end - timedelta(hours=1),
            self.end,
            self.start,
        )
        self.assertEqual(result.state, "fresh")
        self.assertEqual(result.reports_24h, 2)
        self.assertEqual(result.cadence_samples, 4)
        self.assertAlmostEqual(result.p90_gap_seconds, 24 * 3600)

    def test_freshness_late_and_stale(self) -> None:
        reports = [self.end - timedelta(hours=value) for value in range(12, 2, -1)]
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
        self.assertEqual(insufficient.state, "insufficient")
        self.assertEqual(insufficient.issue, ISSUE_INSUFFICIENT_CADENCE)
        future = summarize_freshness(
            [],
            self.end + timedelta(minutes=10),
            self.end,
            self.start,
        )
        self.assertEqual(future.state, "invalid")
        self.assertEqual(future.issue, ISSUE_FUTURE_LAST_SEEN)

    def test_freshness_normalizes_aware_datetimes(self) -> None:
        local_zone = timezone(timedelta(hours=2))
        current = datetime(2026, 9, 14, 11, 30, tzinfo=local_zone)
        reports = [
            datetime(2026, 9, 14, hour, 0, tzinfo=UTC) for hour in (5, 6, 7, 8, 9)
        ]
        result = summarize_freshness(
            reports,
            current,
            self.end,
            self.start,
        )
        self.assertEqual(result.last_seen, datetime(2026, 9, 14, 9, 30, tzinfo=UTC))

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
        self.assertEqual(result.max_positive_delta_24h, 2)
        self.assertEqual(
            result.max_positive_delta_at,
            self.start + timedelta(hours=8),
        )
        self.assertIsNone(result.issue)

    def test_outage_partial_unsupported_and_no_history(self) -> None:
        partial = summarize_outage_history(
            [
                OutageHistoryPoint(self.start + timedelta(hours=2), 14),
                OutageHistoryPoint(self.start + timedelta(hours=3), 15),
            ],
            self.start,
            self.end,
        )
        self.assertEqual(partial.events_24h, 1)
        self.assertEqual(partial.issue, ISSUE_PARTIAL_OUTAGE_WINDOW)

        unsupported = summarize_outage_history(
            [],
            self.start,
            self.end,
            supported=False,
        )
        self.assertFalse(unsupported.supported)
        self.assertIsNone(unsupported.events_24h)

        no_history = summarize_outage_history([], self.start, self.end)
        self.assertTrue(no_history.supported)
        self.assertIsNone(no_history.events_24h)
        self.assertEqual(no_history.issue, ISSUE_NO_OUTAGE_HISTORY)


if __name__ == "__main__":
    unittest.main()
