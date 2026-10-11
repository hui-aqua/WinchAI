import tempfile
import unittest
from pathlib import Path
import numpy as np
from simulation.controls import parse_controls
from simulation.quasistatic import simulate, EquilibriumError, Tolerances
from simulation.winch_solver import run
from tests_python.test_quasistatic import MODEL


class SolverOptionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'model.amodel'
        self.path.write_text(MODEL)

    def test_both_strategies_agree_on_elastic_equilibrium_and_save_times(self):
        results = []
        for strategy in ('time-marching', 'parallel-rope-length'):
            _, result = simulate(self.path, payout_m=.1, duration_seconds=5,
                                 save_interval_seconds=2, workers=1, solver_strategy=strategy)
            np.testing.assert_array_equal(result['frames']['timeS'], [0,2,4,5])
            np.testing.assert_allclose(result['frames']['ropeTensionsN'], 98.0665, atol=1e-4)
            self.assertEqual(result['settings']['equilibriumStrategy'], strategy)
            for metric in result['convergence']:
                self.assertLessEqual(metric['forceResidualN'], 1)
                self.assertLessEqual(metric['momentResidualNm'], 1)
                self.assertLessEqual(metric['constraintErrorM'], 1e-8)
            results.append(result)
        self.assertIsNone(results[0]['equilibriumDatabase'])
        self.assertIsNotNone(results[1]['equilibriumDatabase'])
        np.testing.assert_allclose(results[0]['frames']['nodePositionsM'], results[1]['frames']['nodePositionsM'], atol=1e-6)

    def test_time_marching_handles_waterline_transition(self):
        xml = MODEL.replace('z="0" fixed=', 'z="1" fixed=').replace('z="-2"','z="-0.1"')
        xml = xml.replace('weightInWater="10"','weightInWater="5"').replace(
            '<beam id="1" name="beam">',
            '<beam id="1" name="beam"><wizard type="circular" outerDiameter="100" thickness="10" massDensity="1000"/>')
        self.path.write_text(xml)
        _, result = simulate(self.path, payout_m=-.2, solver_strategy='time-marching')
        self.assertGreater(result['settings']['rejectedSteps'], 0)
        self.assertGreater(result['settings']['eventSteps'], 0)
        self.assertGreater(result['frames']['nodePositionsM'][-1,2,2], 0)

    def test_time_marching_supports_reversals_and_zero_payout(self):
        profile = [{'timeS':0,'velocityMPerS':.02}, {'timeS':5,'velocityMPerS':-.02}]
        _, result = simulate(self.path, payout_m=.1, duration_seconds=5, save_interval_seconds=1,
                             velocity_profile=profile, solver_strategy='time-marching')
        self.assertLess(result['frames']['takeUpM'][2], 0)
        np.testing.assert_allclose(result['frames']['nodePositionsM'][0], result['frames']['nodePositionsM'][-1], atol=1e-6)
        _, held = simulate(self.path, payout_m=0, duration_seconds=5, save_interval_seconds=2, solver_strategy='time-marching')
        np.testing.assert_array_equal(held['frames']['timeS'], [0,2,4,5])
        for state in held['frames']['nodePositionsM']:
            np.testing.assert_array_equal(state, held['frames']['nodePositionsM'][0])

    def test_controls_validation_and_failed_march_does_not_write(self):
        controls = dict(durationSeconds=5, saveIntervalSeconds=2,
                        winches=[dict(componentId=2,payoutM=.1)], solverStrategy='time-marching')
        self.assertEqual(parse_controls(controls)['solver_strategy'], 'time-marching')
        with self.assertRaisesRegex(ValueError, 'Solver strategy'):
            parse_controls(dict(controls, solverStrategy='unknown'))
        with self.assertRaisesRegex(ValueError, 'Solver strategy'):
            simulate(self.path, solver_strategy='unknown')
        self.path.write_text(MODEL.replace('weightInWater="10"','weightInWater="-100"'))
        output = self.path.with_suffix('.aqwr')
        with self.assertRaises(EquilibriumError):
            run(self.path, output, payout_m=0, solver_strategy='time-marching', tolerances=Tolerances(max_evaluations=10))
        self.assertFalse(output.exists())
