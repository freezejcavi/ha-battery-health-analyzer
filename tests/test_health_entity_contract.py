"""Static contract checks for dev25 published Health Model v2 entities."""

from __future__ import annotations

import unittest

from custom_components.battery_health.health_v2 import CONDITION_STATES


class HealthEntityContractTests(unittest.TestCase):
    """Keep the public condition contract intentionally small and stable."""

    def test_public_health_states_are_exactly_four_without_unknown(self) -> None:
        self.assertEqual(
            CONDITION_STATES,
            ("ok", "declining", "weakening", "replace"),
        )
        self.assertNotIn("unknown", CONDITION_STATES)


if __name__ == "__main__":
    unittest.main()
