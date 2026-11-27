"""evalkit.legacy against the 1.0 originals and the 2.0 block set."""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from . import support
from . import v1_harness
from buildarena1.evalkit import legacy as L
from buildarena1.evalkit.config import V1_BLOCK_PARAMS


class AgainstV1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v1 = v1_harness.enter()
        cls.blocks = cls.v1["build"].Blocks().blocks
        cls.root = Path(v1_harness.V1_ROOT)

    def test_mass_table(self):
        expected = {name: spec["weight"] for name, spec in self.blocks.items() if name != "Ballast"}
        self.assertEqual(L.V1_MASS, expected)
        for name in L.V1_MASS:
            self.assertIs(type(L.V1_MASS[name]), type(expected[name]), name)

    def test_actions(self):
        powered = {name: tuple(spec["locomotion"].keys()) for name, spec in self.blocks.items() if spec["locomotion"]}
        self.assertEqual(L.V1_ACTIONS, powered)

    def test_ballast_transform(self):
        ballast = self.v1["build"].Blocks().get(block_name="Ballast", local_id="Ballast")
        self.assertEqual(tuple(float(v) for v in ballast.geo.scale.virtual), L.BALLAST_SCALE_DEFAULT)
        ballast.rotate(yaw=0, pitch=270, roll=0)
        q = ballast.geo.rotation.quat
        self.assertEqual((q.x, q.y, q.z, q.w), L.BALLAST_ROTATION_XYZW)
        data = ET.fromstring(f"<D>{self.blocks['Ballast']['data']}</D>")
        self.assertEqual(data.find("Single[@key='bmt-mass']").text, L.BALLAST_DATA_MASS)

    def test_support_rotation(self):
        q = self.v1["components"].Orientation(rot=[90, 0, 0]).quat
        self.assertEqual((q.x, q.y, q.z, q.w), L.SUPPORT_ROTATED_XYZW)

    def test_backfilled_parameters_are_v1_values(self):
        for v1_name, type_id in (("Powered Wheel", "2"), ("Water Cannon", "56")):
            text = self.blocks[v1_name]["data"].format(flipped=False, spinning_forward="", spinning_backward="",
                                                        hold_to_fire="")
            nodes = {n.get("key"): (n.tag, n.text) for n in ET.fromstring(f"<D>{text}</D>")}
            ours = {key: (tag, value) for tag, key, value in V1_BLOCK_PARAMS[type_id]}
            left_out = set(nodes) - set(ours)
            self.assertEqual(left_out, {"flipped", "bmt-forward", "bmt-backward"} if type_id == "2" else {"bmt-shoot"})
            for key, value in ours.items():
                self.assertEqual(nodes[key], value, key)

    def test_durations_ramps_and_lift_control_in_source(self):
        src = lambda p: (self.root / p).read_text(encoding="utf-8")
        self.assertIn("duration=30, set_ground=True", src("simulation/simulation_transport.py"))
        self.assertIn("duration=17, set_ground=False", src("simulation/simulation_support.py"))
        self.assertIn('duration=10 if level == "soft" else 30', src("simulation/simulation_lift.py"))
        self.assertIn('add_control_sequence(time=2, key="Alpha1", hold_for=30)', src("simulation/simulation_lift.py"))
        build = src("spatial/build.py")
        for line in ("local mass_min = 5.0", "local mass_max = 1500.0", "local ramp_duration = 15.0",
                     "local mass_max = 50.0", "local ramp_duration = 2.0",
                     "math.max(math.min((current_time - 0.5) / ramp_duration, 1.0), 0.005)"):
            self.assertIn(line, build)
        self.assertEqual((L.TRANSPORT_RAMP.mass_max, L.TRANSPORT_RAMP.ramp_duration), (50.0, 2.0))
        self.assertEqual((L.SUPPORT_RAMP.mass_max, L.SUPPORT_RAMP.ramp_duration), (1500.0, 15.0))
        self.assertEqual(L.DURATION[("lift", "soft")], 10.0)
        self.assertEqual(re.findall(r"gap_length = (\d+\.\d)", src("simulation/simulation_support.py")),
                         ["5.0", "10.0", "20.0"])

    def test_keys(self):
        self.assertEqual(L.ARROW_KEYS, tuple(self.v1["build"].AvailableKeys[:4]))


class AgainstV2(unittest.TestCase):
    def test_names_and_channels_exist_in_2_0(self):
        support.v2_ready()
        from buildarena.build import Blocks
        from blocks.control_descriptors import mechanical, weapons
        names = Blocks().blocks
        for v2_name in L.V2_TO_V1_NAME:
            self.assertIn(v2_name, names)
        self.assertIn("Ballast", names)
        self.assertEqual(str(names["Ballast"]["id"]), L.BALLAST_BLOCK_TYPE_ID)
        self.assertEqual(set(mechanical.CONTROL_SEMANTICS[2]["descriptions"]), {"ForwardKey", "BackwardKey"})
        self.assertIn("ShootKey", weapons.CONTROL_SEMANTICS[56]["descriptions"])
        self.assertIn("mass", mechanical.CONTROL_SEMANTICS[35]["sliders"])


if __name__ == "__main__":
    unittest.main()
