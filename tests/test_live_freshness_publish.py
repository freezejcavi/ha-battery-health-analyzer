"""Regression tests for live last_seen publication throttling."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from custom_components.battery_health.coordinator import _freshness_publish_required
from custom_components.battery_health.operability import FreshnessEvidence


def _freshness(
    state: str,
    *,
    age: float = 0.0,
    reports: int = 10,
    issue: str | None = None,
) -> FreshnessEvidence:
    return FreshnessEvidence(
        supported=True,
        last_seen=datetime(2026, 9, 15, 16, 0, tzinfo=UTC),
        source="current",
        age_seconds=age,
        reports_24h=reports,
        cadence_samples=10,
        median_gap_seconds=900.0,
        p90_gap_seconds=1200.0,
        age_to_p90_ratio=age / 1200.0,
        state=state,
        issue=issue,
    )


class LiveFreshnessPublishTests(unittest.TestCase):
    def test_first_live_evidence_is_published(self) -> None:
        self.assertTrue(_freshness_publish_required(None, _freshness("fresh")))

    def test_same_fresh_state_does_not_republish_raw_telemetry_churn(self) -> None:
        previous = _freshness("fresh", age=300.0, reports=10)
        current = _freshness("fresh", age=0.0, reports=11)
        self.assertFalse(_freshness_publish_required(previous, current))

    def test_gate_transition_is_published(self) -> None:
        previous = _freshness("insufficient", issue="insufficient_cadence")
        current = _freshness("fresh")
        self.assertTrue(_freshness_publish_required(previous, current))

    def test_issue_transition_is_published(self) -> None:
        previous = _freshness("insufficient", issue="first_issue")
        current = _freshness("insufficient", issue="second_issue")
        self.assertTrue(_freshness_publish_required(previous, current))


if __name__ == "__main__":
    unittest.main()
