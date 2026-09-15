"""Pure tests for dev25 Health Model v2 summary semantics."""

from __future__ import annotations

import unittest

from custom_components.battery_health.health_v2 import summarize_condition_states


class HealthV2SummaryTests(unittest.TestCase):
    def test_actionable_precedence(self) -> None:
        state, counts, unavailable = summarize_condition_states(
            ["ok", "declining", "weakening", "replace", None]
        )
        self.assertEqual(state, "replace")
        self.assertEqual(
            counts,
            {"ok": 1, "declining": 1, "weakening": 1, "replace": 1},
        )
        self.assertEqual(unavailable, 1)

    def test_declining_outranks_ok(self) -> None:
        state, counts, unavailable = summarize_condition_states(["ok", "declining"])
        self.assertEqual(state, "declining")
        self.assertEqual(counts["declining"], 1)
        self.assertEqual(unavailable, 0)

    def test_no_condition_maps_to_summary_unavailable(self) -> None:
        state, counts, unavailable = summarize_condition_states([None, "unexpected"])
        self.assertIsNone(state)
        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual(unavailable, 2)


if __name__ == "__main__":
    unittest.main()
