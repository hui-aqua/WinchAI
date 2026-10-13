import sys
import unittest
from pathlib import Path

from simulation.local_server import (
    SimulationSettings,
    build_solver_command,
    parse_simulation_settings,
)


class LocalServerSettingsTests(unittest.TestCase):
    def test_parses_quasi_static_settings(self):
        settings = parse_simulation_settings({
            "payout_m": ["2"],
            "initial_increment_m": ["0.05"],
        })
        self.assertEqual(settings, SimulationSettings(2.0, 0.05))

    def test_rejects_missing_and_out_of_range_settings(self):
        with self.assertRaisesRegex(ValueError, "Missing required"):
            parse_simulation_settings({"payout_m": ["1"]})
        with self.assertRaisesRegex(ValueError, "between 1 mm and 50 mm"):
            parse_simulation_settings({"payout_m": ["1"], "initial_increment_m": ["0.1"]})

    def test_rejects_non_finite_and_accepts_retrieval(self):
        query = {
            "payout_m": ["nan"],
            "initial_increment_m": ["0.05"],
        }
        with self.assertRaisesRegex(ValueError, "finite"):
            parse_simulation_settings(query)
        query["payout_m"] = ["-1"]
        self.assertEqual(parse_simulation_settings(query).payout_m, -1)

    def test_builds_argument_list_without_shell_interpolation(self):
        settings = SimulationSettings(1.0, 0.05)
        command = build_solver_command(Path("input model.amodel"), Path("result.aqwr"), settings)
        self.assertEqual(command[:3], [sys.executable, "-m", "simulation.winch_solver"])
        self.assertIn("input model.amodel", command)
        self.assertEqual(command[-4:], ["--payout-m", "1.0", "--initial-increment-mm", "50.0"])


if __name__ == "__main__":
    unittest.main()
