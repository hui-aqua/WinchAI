import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from simulation.quasistatic import simulate, EquilibriumError
from simulation.quasistatic.model import load_model
from simulation.quasistatic.equilibrium import EquilibriumSystem
from simulation.quasistatic.database import EquilibriumDatabase
from simulation.quasistatic.schedule import VelocitySchedule
from tests_python.test_quasistatic import MODEL


class ParallelEquilibriumTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'model.amodel'
        self.path.write_text(MODEL)

    def test_parallel_matches_serial_and_sampling_ignores_playback_clock(self):
        _, serial = simulate(self.path, payout_m=.1, workers=1, time_step_s=1)
        _, parallel = simulate(self.path, payout_m=.1, workers=2, time_step_s=2)
        self.assertEqual(parallel['settings']['parallelWorkers'], 2)
        self.assertEqual(serial['settings']['equilibriumStates'], parallel['settings']['equilibriumStates'])
        for a, b in zip(serial['equilibriumDatabase']['states'], parallel['equilibriumDatabase']['states']):
            np.testing.assert_allclose(a['generalizedCoordinates'], b['generalizedCoordinates'], atol=1e-8)
        np.testing.assert_allclose(serial['frames']['nodePositionsM'][-1], parallel['frames']['nodePositionsM'][-1])
        self.assertEqual(parallel['settings']['integrationStepSeconds'], 0)
        database = parallel['equilibriumDatabase']
        np.testing.assert_allclose(database['states'][-1]['ropeLengthsM'], [[2.1], [2.1]])
        np.testing.assert_allclose(database['states'][-1]['ropeTensionsN'], 98.0665, atol=1e-4)

    def test_failed_independent_state_is_repaired_from_neighbor(self):
        system = EquilibriumSystem(load_model(self.path))
        solve = system.solve
        def fail_without_neighbor(payout, previous=None):
            if previous is None and payout[0] > .04:
                raise EquilibriumError('test independent seed failure')
            return solve(payout, previous)
        with patch.object(system, 'solve', side_effect=fail_without_neighbor):
            database = EquilibriumDatabase(system, np.array([.1, .1]), .05, .0001, 100, 1)
        self.assertGreaterEqual(database.repairs, 2)
        self.assertEqual(len(database.samples), 3)
        for _, state, _ in database.samples.values():
            self.assertLess(state['forceError'], 1)

    def test_reversal_and_pause_reuse_precomputed_range(self):
        profile = [{'timeS':0,'velocityMPerS':.05}, {'timeS':2,'velocityMPerS':.05},
                   {'timeS':3,'velocityMPerS':0}, {'timeS':4,'velocityMPerS':0},
                   {'timeS':5,'velocityMPerS':-.05}, {'timeS':7,'velocityMPerS':-.05}]
        _, result = simulate(self.path, payout_m=.2, duration_seconds=7,
                             save_interval_seconds=1, velocity_profile=profile, workers=1)
        takeup = result['frames']['takeUpM']
        self.assertAlmostEqual(takeup[3], takeup[4])
        self.assertLess(takeup[2], takeup[0])
        self.assertGreater(takeup[-1], takeup[5])
        np.testing.assert_allclose(result['frames']['nodePositionsM'][0], result['frames']['nodePositionsM'][-1])

    def test_schedule_rejects_interior_overshoot_and_bad_times(self):
        for profile in ([{'timeS':0,'velocityMPerS':1},{'timeS':1,'velocityMPerS':-1}],
                        [{'timeS':0,'velocityMPerS':0},{'timeS':0,'velocityMPerS':0}],
                        [{'timeS':0,'velocityMPerS':float('nan')},{'timeS':1,'velocityMPerS':0}]):
            with self.assertRaises(ValueError):
                VelocitySchedule(1, .1, profile)

    def test_minimum_increment_rejects_unresolved_interpolation(self):
        system = EquilibriumSystem(load_model(self.path))
        evaluate = system.evaluate
        def invalid_midpoint(x, payout):
            state = evaluate(x, payout)
            if np.isclose(payout[0], .025):
                state['forceError'] = 2
            return state
        with patch.object(system, 'evaluate', side_effect=invalid_midpoint):
            with self.assertRaisesRegex(EquilibriumError, 'minimum rope increment'):
                EquilibriumDatabase(system, np.array([.05, .05]), .05, .05, 100, 1)

    def test_trial_budget_is_bounded(self):
        with self.assertRaisesRegex(EquilibriumError, 'maximum trial steps'):
            simulate(self.path, payout_m=1, max_steps=2, workers=1)
