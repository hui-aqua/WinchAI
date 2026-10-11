import json
import struct
import tempfile
import unittest
from pathlib import Path
import numpy as np
from simulation.quasistatic import simulate, EquilibriumError, Tolerances
from simulation.quasistatic.hydrostatics import segment_buoyancy, circular_fraction
from simulation.result_format import encode_result
from simulation.winch_solver import run

# Two supports hold a horizontal massive rigid beam, without buoyancy.
MODEL = '''<model name="static-test"><Nodes>
<node id="1" x="-1" y="0" z="0" fixed="true"/>
<node id="2" x="1" y="0" z="0" fixed="true"/>
<node id="3" x="-1" y="0" z="-2"/>
<node id="4" x="1" y="0" z="-2"/>
</Nodes><Components>
<beam id="1" name="beam"><materials weightInAir="10" weightInWater="10"/>
<element id="1" StartNode_ID="3" EndNode_ID="4"/></beam>
<truss id="2" name="winch1"><mooring areal="0.001" young="1e8" massdensity="1000" weightInAir="0" weightInWater="0"/>
<element id="2" StartNode_ID="1" EndNode_ID="3"/></truss>
<truss id="3" name="winch2"><mooring areal="0.001" young="1e8" massdensity="1000" weightInAir="0" weightInWater="0"/>
<element id="3" StartNode_ID="2" EndNode_ID="4"/></truss>
</Components></model>'''


class QuasiStaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'model.amodel'
        self.path.write_text(MODEL)

    def test_analytical_elastic_equilibrium_and_both_directions(self):
        for payout in (0.1,-0.1,0.0):
            data,result = simulate(self.path,payout_m=payout)
            frames = result['frames']
            tension = 10*9.80665
            np.testing.assert_allclose(frames['ropeTensionsN'],tension,atol=1e-4)
            expected = -(2+payout)*(1+tension/1e5)
            np.testing.assert_allclose(frames['nodePositionsM'][-1,2:,2],expected,atol=1e-6)
            np.testing.assert_array_equal(frames['nodePositionsM'][:,0:2],
                                          np.broadcast_to(np.asarray(data['nodePositionsM'])[:2],frames['nodePositionsM'][:,0:2].shape))
            self.assertAlmostEqual(frames['takeUpM'][-1],-payout)
            for metric in result['convergence']:
                self.assertLessEqual(metric['forceResidualN'],1)
                self.assertLessEqual(metric['momentResidualNm'],1)
                self.assertLessEqual(metric['constraintErrorM'],1e-8)

    def test_free_surface_force_and_centroid(self):
        force,center = segment_buoyancy(np.array([0.,0.,-1.]),np.array([0.,0.,1.]),100)
        self.assertAlmostEqual(force,50)
        self.assertAlmostEqual(center[2],-0.5)
        self.assertAlmostEqual(float(circular_fraction(0,0.5)),0.5)
        for z,expected in ((-1,100),(0,50),(1,0)):
            force,_ = segment_buoyancy(np.array([0.,0.,z]),np.array([1.,0.,z]),100,0.5)
            self.assertAlmostEqual(force,expected,places=6)

    def test_waterline_transition_reduces_increment(self):
        xml = MODEL.replace('z="0" fixed=', 'z="1" fixed=').replace('z="-2"','z="-0.1"')
        xml = xml.replace('weightInWater="10"','weightInWater="5"').replace(
            '<beam id="1" name="beam">',
            '<beam id="1" name="beam"><wizard type="circular" outerDiameter="100" thickness="10" massDensity="1000"/>')
        self.path.write_text(xml)
        _, result = simulate(self.path,payout_m=-0.2)
        self.assertGreater(result['settings']['rejectedSteps'],0)
        self.assertGreater(result['settings']['eventSteps'],0)
        self.assertTrue(any(m.get('incrementM',1) < 0.05 for m in result['convergence']))
        self.assertGreater(result['frames']['nodePositionsM'][-1,2,2],0)
        self.assertAlmostEqual(result['frames']['takeUpM'][-1],0.2)

    def test_buoyant_structure_floats_with_slack_winches(self):
        xml = MODEL.replace('z="0" fixed=', 'z="1" fixed=').replace('z="-2"','z="-0.1"')
        xml = xml.replace('weightInWater="10"','weightInWater="-10"').replace(
            '<beam id="1" name="beam">',
            '<beam id="1" name="beam"><wizard type="circular" outerDiameter="100" thickness="10" massDensity="1000"/>')
        self.path.write_text(xml)
        _, result = simulate(self.path,payout_m=0.05)
        np.testing.assert_allclose(result['frames']['ropeTensionsN'],0,atol=1e-5)
        np.testing.assert_allclose(result['frames']['nodePositionsM'][:,2:,2],0,atol=1e-5)
        _, retrieval = simulate(self.path,payout_m=-0.2)
        self.assertGreater(retrieval['frames']['ropeTensionsN'][-1,0],0)
        self.assertGreater(retrieval['settings']['eventSteps'],0)
        self.assertGreater(retrieval['frames']['nodePositionsM'][-1,2,2],0.05)

    def test_asymmetric_load_solves_rotational_equilibrium(self):
        self.path.write_text(MODEL.replace(
            '<node id="3" x="-1" y="0" z="-2"/>',
            '<node id="3" x="-1" y="0" z="-2"><pointLoad><Force x="0" y="0" z="-100"/></pointLoad></node>'))
        _, result = simulate(self.path,payout_m=-0.05)
        positions = result['frames']['nodePositionsM'][-1]
        self.assertLess(positions[2,2],positions[3,2])
        self.assertAlmostEqual(np.linalg.norm(positions[3]-positions[2]),2,places=6)
        self.assertLess(result['convergence'][-1]['momentResidualNm'],1)
        self.assertGreater(result['frames']['ropeTensionsN'][-1,0],result['frames']['ropeTensionsN'][-1,1])

    def test_result_preserves_playback_layout_and_diagnostics(self):
        data,result = simulate(self.path,payout_m=-0.05)
        binary = encode_result(data,result)
        version,size = struct.unpack_from('<HI',binary,4)
        header = json.loads(binary[10:10+size])
        self.assertEqual(version,2)
        self.assertEqual(header['settings']['solverMethod'],'quasi-static')
        self.assertEqual(len(header['convergence']),header['frameCount'])
        self.assertEqual(len(binary),10+size+header['frameCount']*header['frameRecordBytes'])

    def test_failed_equilibrium_does_not_write_result(self):
        self.path.write_text(MODEL.replace('weightInWater="10"','weightInWater="-100"'))
        output = Path(self.temp.name)/'failed.aqwr'
        with self.assertRaises(EquilibriumError):
            run(self.path,output,payout_m=0,tolerances=Tolerances(max_evaluations=10))
        self.assertFalse(output.exists())

    def test_disconnected_beam_sections_do_not_inherit_supports(self):
        from simulation.quasistatic.model import load_model
        xml = MODEL.replace('</Nodes>', '<node id="5" x="-1" y="1" z="0"/></Nodes>')
        xml = xml.replace('</beam>', '<element id="4" StartNode_ID="1" EndNode_ID="5"/></beam>')
        self.path.write_text(xml)
        model = load_model(self.path)
        self.assertEqual(model.metadata['fixedNodeIds'],[1,2,5])
        self.assertEqual(len(model.bodies),1)
        np.testing.assert_array_equal(model.bodies[0].nodes,[2,3])

    def test_invalid_model_data_has_actionable_errors(self):
        for xml,message in (
            (MODEL.replace('</Nodes>', '<node id="1" x="0" y="0" z="0"/></Nodes>'),'Duplicate node'),
            (MODEL.replace('EndNode_ID="4"','EndNode_ID="999"'),'unknown node'),
            (MODEL.replace('truss id="3"','truss id="2"'),'Duplicate component'),
            (MODEL.replace('weightInAir="0"','weightInAir="-1"'),'nonnegative')):
            self.path.write_text(xml)
            with self.assertRaisesRegex(ValueError,message):
                simulate(self.path,payout_m=0)

    def test_invalid_controls_and_excess_retrieval(self):
        for kwargs in ({'payout_m':float('nan')},{'initial_increment_m':0},
                       {'speed_m_per_min':0},{'payout_m':-3}, {'max_steps':0}):
            with self.assertRaises(ValueError):
                simulate(self.path,**kwargs)


if __name__ == '__main__':
    unittest.main()
