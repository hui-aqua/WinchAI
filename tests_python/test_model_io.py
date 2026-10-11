import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from simulation.model_io import _beam_properties, _read_model


class WinchModelTests(unittest.TestCase):
    def test_hollow_beam_area_and_displaced_area_use_millimeter_model_inputs(self):
        beam = ET.Element("beam")
        ET.SubElement(beam, "wizard", {
            "type": "circular",
            "outerDiameter": "500",
            "thickness": "50",
            "massDensity": "960",
        })
        area, displaced_area, density = _beam_properties(beam)
        self.assertAlmostEqual(area, math.pi * (0.5**2 - 0.4**2) / 4)
        self.assertAlmostEqual(displaced_area, math.pi * 0.5**2 / 4)
        self.assertEqual(density, 960)


    def test_i_beam_uses_the_model_web_thickness(self):
        beam = ET.Element("beam")
        ET.SubElement(beam, "wizard", {
            "type": "ibeam",
            "tfw": "250",
            "wh": "410",
            "wt": "20",
            "tft": "20",
            "bft": "20",
            "massDensity": "960",
        })
        area, envelope_area, _ = _beam_properties(beam)
        self.assertAlmostEqual(area, 0.25 * 0.04 + 0.41 * 0.02)
        self.assertAlmostEqual(envelope_area, area)


    def test_missing_winch_is_an_error_not_a_silent_default(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.amodel"
            model_path.write_text(
                '<model><Nodes><node id="1" x="0" y="0" z="0"/>'
                '<node id="2" x="0" y="0" z="-1"/></Nodes><Components>'
                '<beam id="1" name="ring"><wizard type="circular" outerDiameter="500" '
                'thickness="50" massDensity="1000"/><element id="1" StartNode_ID="1" '
                'EndNode_ID="2"/></beam></Components></model>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "winch1"):
                _read_model(model_path, water_density=1025)


    @unittest.skipUnless((Path(__file__).resolve().parents[1] / 'models/Next_Otervika_NLS_AI.amodel').is_file(), 'Local target model unavailable')
    def test_winch_top_attachments_fix_their_rigid_beam_assembly_only(self):
        target = Path(__file__).resolve().parents[1] / "models" / "Next_Otervika_NLS_AI.amodel"
        data = _read_model(target, water_density=1025)
        nodes = {
            int(node.get("id")): float(node.get("z"))
            for node in ET.parse(target).getroot().findall("./Nodes/node")
        }
        self.assertEqual(len(data["surfaceNodeIds"]), 360)
        winch_top_ids = {node_id for winch in data["winches"] for node_id in winch["topNodeIds"]}
        self.assertEqual(len(winch_top_ids), 20)
        self.assertEqual(len(data["fixedNodeIds"]), 360)
        self.assertTrue(winch_top_ids.issubset(data["fixedNodeIds"]))
        self.assertTrue(all(nodes[node_id] == 0 for node_id in winch_top_ids))
        self.assertEqual(set(data["surfaceNodeIds"]), set(data["fixedNodeIds"]))
        self.assertTrue(any("Rigid beams held fixed:" in assumption for assumption in data["assumptions"]))
        self.assertTrue(any("only explicit supports and winch upper attachments constrain motion" in assumption for assumption in data["assumptions"]))
        beam_motion = next(assumption for assumption in data["assumptions"] if assumption.startswith("Rigid beams held fixed:"))
        self.assertIn("Flytekrage_", beam_motion)
        self.assertIn("Bunnring", beam_motion.partition("; translated:")[2])
        self.assertFalse(any("spans supported and free nodes" in warning for warning in data["warnings"]))


    @unittest.skipUnless((Path(__file__).resolve().parents[1] / 'models/Next_Otervika_NLS_AI.amodel').is_file(), 'Local target model unavailable')
    def test_target_ring_weight_and_buoyancy_use_model_line_mass_inputs(self):
        target = Path(__file__).resolve().parents[1] / "models" / "Next_Otervika_NLS_AI.amodel"
        data = _read_model(target, water_density=1025)
        by_name = {beam["name"].split()[0]: beam for beam in data["beamLoads"]}
        expected = {
            "Bunnring": (44463.076946, 400169.6642),
            "NEXT-ring": (113619.08717, 896452.7743),
            "Innfestingsring": (1034.053798, 19899.48791),
        }
        for name, (mass, buoyancy) in expected.items():
            self.assertAlmostEqual(by_name[name]["massKg"], mass, delta=0.1)
            self.assertAlmostEqual(by_name[name]["buoyancyN"], buoyancy, delta=1.0)
