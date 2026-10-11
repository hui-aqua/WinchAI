import json
import struct
import tempfile
import unittest
from pathlib import Path
import numpy as np
from tests_python.test_quasistatic import MODEL
from simulation.controls import parse_controls
from simulation.quasistatic import simulate
from simulation.quasistatic.model import load_model
from simulation.quasistatic.properties import component_properties, apply_component_overrides
from simulation.result_format import encode_result

class ControlledSimulationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'model.amodel'
        self.path.write_text(MODEL)

    def test_properties_and_override_elasticity(self):
        rows = component_properties(load_model(self.path))
        self.assertEqual(rows[0]['massKg'],20)
        self.assertAlmostEqual(rows[0]['weightInAirN'],196.133)
        self.assertEqual(rows[1]['eaN'],100000)
        self.assertEqual(rows[1]['pathLengthsM'],[2])
        data,result = simulate(self.path,winch_payouts={2:0,3:0},duration_seconds=5,save_interval_seconds=2,
                              component_overrides=[{'componentId':2,'eaN':200000},{'componentId':3,'eaN':200000}])
        np.testing.assert_allclose(result['frames']['nodePositionsM'][-1,2:,2],-2*(1+98.0665/200000),atol=1e-6)
        self.assertEqual(data['componentProperties'][1]['eaN'],200000)
        self.assertEqual(self.path.read_text(),MODEL)
        binary = encode_result(data,result)
        header = json.loads(binary[10:10+struct.unpack('<I',binary[6:10])[0]])
        self.assertEqual(header['settings']['frameIntervalSeconds'],2)
        self.assertEqual(header['componentProperties'][1]['eaN'],200000)

    def test_reference_length_edits_change_unstretched_length(self):
        _,result = simulate(self.path,winch_payouts={2:.1,3:.1},duration_seconds=4,save_interval_seconds=1.5,
                              component_overrides=[{'componentId':2,'lengthM':2.2},{'componentId':3,'lengthM':2.2}])
        np.testing.assert_array_equal(result['frames']['timeS'],[0,1.5,3,4])
        np.testing.assert_allclose(result['frames']['nodePositionsM'][-1,2:,2],-2.3*(1+98.0665/100000),atol=1e-6)

    def test_independent_payouts_and_exact_save_times(self):
        _,result = simulate(self.path,winch_payouts={2:.1,3:-.1},duration_seconds=5,save_interval_seconds=2)
        np.testing.assert_array_equal(result['frames']['timeS'],[0,2,4,5])
        for time,metric in zip([0,2,4,5],result['convergence']):
            np.testing.assert_allclose(metric['winchPayoutsM'],[.1*time/5,-.1*time/5])
        self.assertLess(result['frames']['nodePositionsM'][-1,2,2],result['frames']['nodePositionsM'][-1,3,2])
        self.assertGreaterEqual(result['settings']['acceptedSteps'],3)

    def test_zero_payout_holds_equilibrium_for_requested_duration(self):
        _,result = simulate(self.path,winch_payouts={2:0,3:0},duration_seconds=5,save_interval_seconds=2)
        np.testing.assert_array_equal(result['frames']['timeS'],[0,2,4,5])
        for positions in result['frames']['nodePositionsM']:
            np.testing.assert_array_equal(positions,result['frames']['nodePositionsM'][0])

    def test_validation_and_edited_mass(self):
        for override in ({'componentId':1,'lengthM':3},{'componentId':2,'eaN':0},
                         {'componentId':1,'massKg':1,'weightInAirN':100}, {'componentId':999,'massKg':2}):
            with self.assertRaises(ValueError): apply_component_overrides(load_model(self.path),[override])
        data,result = simulate(self.path,winch_payouts={2:0,3:0},duration_seconds=1,save_interval_seconds=1,
                              component_overrides=[{'componentId':1,'massKg':40,'weightInWaterN':392.266}])
        self.assertEqual(data['totalMassKg'],40)
        np.testing.assert_allclose(result['frames']['ropeTensionsN'],196.133,atol=1e-4)
        for delta in (0,-1,float('nan')):
            with self.assertRaises(ValueError):
                parse_controls({'durationSeconds':5,'saveIntervalSeconds':delta,'winches':[{'componentId':2,'payoutM':0}]})
        with self.assertRaisesRegex(ValueError,'result limit'):
            simulate(self.path,winch_payouts={2:0,3:0},duration_seconds=100,save_interval_seconds=.001,max_result_bytes=1000)

class LocalControlApiTests(unittest.TestCase):
    def test_inspection_and_json_simulation(self):
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from simulation.local_server import LocalAppHandler
        from unittest.mock import patch
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        run_directory = Path(temporary.name)/'runs'
        patcher = patch('simulation.local_server.RUNS_DIRECTORY',run_directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        server = ThreadingHTTPServer(('127.0.0.1',0),LocalAppHandler)
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            connection = HTTPConnection('127.0.0.1',server.server_port,timeout=30)
            connection.request('POST','/api/model-properties',MODEL,{'X-WinchAI-Request':'1'})
            response = connection.getresponse()
            self.assertEqual(response.status,200)
            rows = json.loads(response.read())
            self.assertEqual(rows['components'][1]['eaN'],100000)
            self.assertEqual(rows['winches'][0]['pathLengthsM'],[2])
            body = json.dumps({'modelXml':MODEL,'controls':{'durationSeconds':5,'saveIntervalSeconds':2,
                              'winches':[{'componentId':2,'payoutM':0},{'componentId':3,'payoutM':0}]}})
            connection.request('POST','/api/simulate',body,{'X-WinchAI-Request':'1','Content-Type':'application/json'})
            response = connection.getresponse()
            binary = response.read()
            self.assertEqual(response.status,200,binary)
            header = json.loads(binary[10:10+struct.unpack('<I',binary[6:10])[0]])
            self.assertEqual(header['frameCount'],4)
            self.assertEqual(header['settings']['durationSeconds'],5)
            saved = next(run_directory.iterdir())
            report = json.loads((saved/'diagnostics.json').read_text())
            self.assertEqual(report['status'],'succeeded')
            self.assertEqual(report['exitCode'],0)
            self.assertTrue((saved/'stdout.log').is_file())
            self.assertTrue((saved/'input.amodel').is_file())
            with patch('simulation.local_server.subprocess.run',side_effect=RuntimeError('test server failure')):
                connection.request('POST','/api/simulate',body,{'X-WinchAI-Request':'1','Content-Type':'application/json'})
                response = connection.getresponse()
                failure = json.loads(response.read())
                self.assertEqual(response.status,500)
                self.assertIn('test server failure',failure['error'])
                failed = Path(failure['diagnosticsPath'])
                self.assertTrue((failed/'traceback.log').is_file())
                self.assertEqual(json.loads((failed/'diagnostics.json').read_text())['status'],'failed')
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
