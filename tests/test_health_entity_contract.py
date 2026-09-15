"""Static contract checks for dev23 published health entities."""

from __future__ import annotations

import unittest

from custom_components.battery_health.health import HEALTH_STATES


class HealthEntityContractTests(unittest.TestCase):
    """Keep the public state contract intentionally small and stable."""

    def test_public_health_states_are_exactly_four(self) -> None:
        self.assertEqual(
            HEALTH_STATES,
            ("ok", "weakening", "replace", "unknown"),
        )


if __name__ == "__main__":
    unittest.main()
