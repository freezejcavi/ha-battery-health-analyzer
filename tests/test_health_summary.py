"""Pure tests for dev23 aggregate health state semantics."""

from __future__ import annotations

import unittest

from custom_components.battery_health.health import summarize_health_states


class HealthSummaryTests(unittest.TestCase):
    """Verify actionable aggregate-state precedence."""

    def test_replace_outranks_all_other_states(self) -> None:
        state, counts = summarize_health_states(
            ["ok", "unknown", "weakening", "replace"]
        )
        self.assertEqual(state, "replace")
        self.assertEqual(counts["replace"], 1)

    def test_weakening_outranks_unknown(self) -> None:
        state, counts = summarize_health_states(["ok", "unknown", "weakening"])
        self.assertEqual(state, "weakening")
        self.assertEqual(counts["unknown"], 1)

    def test_unknown_outranks_ok_when_no_actionable_state_exists(self) -> None:
        state, _counts = summarize_health_states(["ok", "unknown"])
        self.assertEqual(state, "unknown")

    def test_all_ok_is_ok(self) -> None:
        state, counts = summarize_health_states(["ok", "ok"])
        self.assertEqual(state, "ok")
        self.assertEqual(counts["ok"], 2)

    def test_empty_or_unexpected_input_is_unknown(self) -> None:
        empty_state, empty_counts = summarize_health_states([])
        bad_state, bad_counts = summarize_health_states(["unexpected"])
        self.assertEqual(empty_state, "unknown")
        self.assertEqual(sum(empty_counts.values()), 0)
        self.assertEqual(bad_state, "unknown")
        self.assertEqual(bad_counts["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
