import json
import math
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from simulation.rope_dynamics import simulate_model
from simulation.winch_solver import FRAME, MAGIC, _beam_properties, _read_model, encode_result


class WinchSolverTests(unittest.TestCase):
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
        self.assertAlmostEqual(envelope_area, 0.25 * (0.41 + 0.04))

    def test_result_is_binary_and_takeup_is_one_meter_per_minute(self):
        data, dynamic = self._dynamic_model()
        result = encode_result(data, dynamic)
        self.assertEqual(result[:4], MAGIC)
        version, header_size = struct.unpack_from("<HI", result, 4)
        self.assertEqual(version, 2)
        header = json.loads(result[10 : 10 + header_size])
        self.assertEqual(header["frameCount"], 3)
        self.assertEqual(header["frameRecordBytes"], FRAME.size + 3 * 3 * 4 + 2 * 4)
        self.assertEqual(header["fixedNodeIds"], [1])
        frame_size = header["frameRecordBytes"]
        frame_values = FRAME.unpack_from(result, 10 + header_size + 2 * frame_size)
        self.assertEqual(frame_values[0], 0.1)
        self.assertAlmostEqual(frame_values[1], 0.1 / 60)
        self.assertGreater(frame_values[3], 0)
        node_values_offset = 10 + header_size + 2 * frame_size + FRAME.size
        positions = struct.unpack_from("<9f", result, node_values_offset)
        self.assertEqual(positions[2], 0)
        self.assertNotAlmostEqual(positions[5], -1, places=4)
        self.assertAlmostEqual(math.dist(positions[3:6], positions[6:9]), 1.0, places=5)
        tensions = struct.unpack_from("<2f", result, node_values_offset + 3 * 3 * 4)
        self.assertTrue(any(tension > 0 for tension in tensions))
        self.assertAlmostEqual(frame_values[3], tensions[0], places=4)
        self.assertAlmostEqual(frame_values[4], tensions[1], places=4)
        self.assertTrue(any(dynamic["frames"]["ropeTensionsN"][-1] > 0))
        self.assertEqual(len(dynamic["ropeElements"]), 2)

    def test_supported_model_properties_and_ambiguous_settings_are_reported(self):
        data, _ = self._dynamic_model()
        beam = data["beamLoads"][0]
        self.assertGreater(beam["massKg"], 0)
        self.assertGreater(beam["buoyancyN"], 0)
        self.assertTrue(data["warnings"])
        self.assertEqual(len(data["winches"]), 2)
        self.assertEqual(data["winches"][0]["topNodeIds"], [1])
        self.assertEqual(data["fixedNodeIds"], [1])
        self.assertGreater(data["tensionEachN"], 0)
        self.assertTrue(data["assumptions"])

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

    def test_surface_nodes_are_fixed_and_target_beams_separate_into_fixed_and_moving_bodies(self):
        target = Path(__file__).resolve().parents[1] / "public" / "Next_Otervika_NLS_AI.amodel"
        data = _read_model(target, water_density=1025)
        nodes = {
            int(node.get("id")): float(node.get("z"))
            for node in ET.parse(target).getroot().findall("./Nodes/node")
        }
        self.assertEqual(len(data["fixedNodeIds"]), 360)
        self.assertTrue(all(nodes[node_id] == 0 for node_id in data["fixedNodeIds"]))
        self.assertTrue(any("Rigid beams held fixed:" in assumption for assumption in data["assumptions"]))
        beam_motion = next(assumption for assumption in data["assumptions"] if assumption.startswith("Rigid beams held fixed:"))
        self.assertIn("Flytekrage_", beam_motion)
        self.assertIn("Bunnring", beam_motion)
        self.assertFalse(any("spans fixed Z=0 and free nodes" in warning for warning in data["warnings"]))

    def test_target_ring_weight_and_buoyancy_use_model_line_mass_inputs(self):
        target = Path(__file__).resolve().parents[1] / "public" / "Next_Otervika_NLS_AI.amodel"
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

    def _dynamic_model(self):
        xml = """<model name="physics-test">
          <Nodes>
            <node id="1" x="0" y="0" z="0"/>
            <node id="2" x="0" y="0" z="-1"/>
            <node id="3" x="0" y="1" z="-1"/>
          </Nodes>
          <Components>
            <beam id="1" name="test-ring">
              <wizard type="circular" outerDiameter="500" thickness="50" massDensity="4000"/>
              <element id="1" StartNode_ID="2" EndNode_ID="3"/>
            </beam>
            <truss id="2" name="winch1"><mooring areal="0.001" young="1E7" massdensity="1000" weightInAir="1" weightInWater="-0.025" noCompressionForces="true" pretension="0"/><element id="2" StartNode_ID="1" EndNode_ID="2"/></truss>
            <truss id="3" name="winch2"><mooring areal="0.001" young="1E7" massdensity="1000" weightInAir="1" weightInWater="-0.025" noCompressionForces="true" pretension="0"/><element id="3" StartNode_ID="1" EndNode_ID="2"/></truss>
          </Components>
        </model>"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        model_path = Path(directory.name) / "model.amodel"
        model_path.write_text(xml, encoding="utf-8")
        data = _read_model(model_path, water_density=1025)
        dynamic = simulate_model(
            model_path,
            water_density=1025,
            duration_seconds=0.1,
            frame_interval_seconds=0.05,
            max_internal_steps=10000,
        )
        return data, dynamic


    def test_dynamic_membrane_moves_together_with_associated_rope(self):
        xml = """<model name="membrane-test">
          <Nodes>
            <node id="1" x="0" y="0" z="0"/>
            <node id="2" x="0" y="0" z="-2"/>
            <node id="3" x="1" y="0" z="-2"/>
            <node id="4" x="0" y="1" z="-2"/>
          </Nodes>
          <Components>
            <beam id="1" name="test-beam">
              <wizard type="circular" outerDiameter="500" thickness="50" massDensity="4000"/>
              <element id="1" StartNode_ID="2" EndNode_ID="3"/>
            </beam>
            <truss id="2" name="winch1"><mooring areal="0.001" young="1E7" massdensity="1000" weightInAir="1" weightInWater="-0.025" noCompressionForces="true" pretension="0"/><element id="2" StartNode_ID="1" EndNode_ID="2"/></truss>
            <truss id="3" name="winch2"><mooring areal="0.001" young="1E7" massdensity="1000" weightInAir="1" weightInWater="-0.025" noCompressionForces="true" pretension="0"/><element id="3" StartNode_ID="1" EndNode_ID="2"/></truss>
            <membrane id="4" name="test-net" emodule="1E8" areal="2.5E-6" massDensity="1000" weight="-10">
              <element id="4" nodeA="2" nodeB="3" nodeC="4"/>
            </membrane>
          </Components>
        </model>"""
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.amodel"
            model_path.write_text(xml, encoding="utf-8")
            dynamic = simulate_model(
                model_path,
                water_density=1025,
                duration_seconds=0.2,
                frame_interval_seconds=0.1,
                max_internal_steps=50000,
                solver_method="xpbd",
                include_membrane=True,
            )
            self.assertTrue(dynamic["settings"]["membraneDynamics"])
            self.assertEqual(dynamic["settings"]["solverMethod"], "xpbd")
            # Node 4 is a pure membrane node (not in beam or winch rope)
            # Check that node 4 moves together with node 2 & 3 as winch lifts
            initial_pos = dynamic["frames"]["nodePositionsM"][0]
            final_pos = dynamic["frames"]["nodePositionsM"][-1]
            # Node 4 index is 3 (0-indexed for [1, 2, 3, 4])
            node4_initial_z = initial_pos[3, 2]
            node4_final_z = final_pos[3, 2]
            self.assertGreater(node4_final_z, node4_initial_z, "Pure membrane node should move upward with associated rope/beam")

    def test_xpbd_supports_long_duration_without_step_limit_error(self):
        xml = """<model name="long-sim-test">
          <Nodes>
            <node id="1" x="0" y="0" z="0"/>
            <node id="2" x="0" y="0" z="-10"/>
            <node id="3" x="0" y="1" z="-10"/>
          </Nodes>
          <Components>
            <beam id="1" name="weight-beam">
              <wizard type="circular" outerDiameter="500" thickness="50" massDensity="4000"/>
              <element id="1" StartNode_ID="2" EndNode_ID="3"/>
            </beam>
            <truss id="2" name="winch1"><mooring areal="0.001" young="1E8" massdensity="1000" weightInAir="1" weightInWater="-0.025" noCompressionForces="true" pretension="0"/><element id="2" StartNode_ID="1" EndNode_ID="2"/></truss>
            <truss id="3" name="winch2"><mooring areal="0.001" young="1E8" massdensity="1000" weightInAir="1" weightInWater="-0.025" noCompressionForces="true" pretension="0"/><element id="3" StartNode_ID="1" EndNode_ID="2"/></truss>
          </Components>
        </model>"""
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "model.amodel"
            model_path.write_text(xml, encoding="utf-8")
            # 7500 seconds (125 minutes) with XPBD step 0.1s and frame interval 7500s
            dynamic = simulate_model(
                model_path,
                water_density=1025,
                duration_seconds=7500.0,
                frame_interval_seconds=7500.0,
                max_internal_steps=500000,
                solver_method="xpbd",
                step_seconds=0.1,
            )
            self.assertEqual(dynamic["settings"]["durationSeconds"], 7500.0)
            self.assertEqual(dynamic["settings"]["internalSteps"], 75000)
            self.assertEqual(len(dynamic["frames"]["timeS"]), 2)
            self.assertAlmostEqual(dynamic["frames"]["timeS"][-1], 7500.0)

    def test_explicit_solver_option(self):
        data, dynamic = self._dynamic_model()
        self.assertIn("settings", dynamic)
        self.assertIn(dynamic["settings"]["solverMethod"], ["xpbd", "explicit"])


if __name__ == "__main__":
    unittest.main()
