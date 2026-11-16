"""Rebuild from a valid history, place per category, add cargo, back-fill
1.0 parameters, export the BSG and the Build ID / GUID / name table."""

from __future__ import annotations

import json
import types
import unittest
import uuid
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from . import support
from buildarena1.evalkit import legacy as L
from buildarena1.evalkit.config import EvalConfig, V1_BLOCK_PARAMS
from buildarena1.evalkit.rebuild import RebuildError, plan_placement, prepare

EXAMPLES = ("rocket_orbit_return", "transforming_car_aerobatics", "shuttle_booster_recovery")


def floats(node, *keys):
    return tuple(float(node.get(k)) for k in keys)


def data_params(block) -> dict[str, tuple[str, str]]:
    data = block.find("Data")
    return {} if data is None else {n.get("key"): (n.tag, n.text) for n in data}


class Examples(unittest.TestCase):
    """The three histories shipped in 2.0 control/examples."""

    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()
        cls.out = support.scratch("examples_")
        cls.prepared = {}
        for name in EXAMPLES:
            history = cls.paths.v2_root / "control" / "examples" / name / "machine.json"
            cls.prepared[name] = prepare(history, "transport", "medium", EvalConfig(), cls.out / name, name)

    def test_operation_count_matches_history(self):
        for name, prep in self.prepared.items():
            ops = json.loads((self.paths.v2_root / "control/examples" / name / "machine.json").read_text())
            self.assertEqual(prep.manifest["operations"], len(ops), name)
            self.assertEqual(prep.manifest["replayed_operations"], len(ops), name)

    def test_blocks_guids_and_table(self):
        for name, prep in self.prepared.items():
            tree = ET.parse(prep.bsg).getroot()
            blocks = tree.findall("Blocks/Block")
            self.assertEqual(blocks[0].get("id"), "0", name)
            table = prep.blocks
            self.assertEqual(len(blocks), len(table), name)
            self.assertEqual({b.get("guid") for b in blocks}, {r["guid"] for r in table}, name)
            sha = prep.manifest["history_sha256"]
            for row in table:
                key = row["build_id"] if row["build_id"] is not None else "Ballast"
                self.assertEqual(row["guid"], str(uuid.uuid5(uuid.NAMESPACE_URL, f"buildarena:{sha}:{key}")))
            self.assertEqual(table[0]["name_v2"], "Starting Block")
            self.assertEqual(table[-1]["legacy_id"], "Ballast")

    def test_transport_position_and_cargo(self):
        for name, prep in self.prepared.items():
            tree = ET.parse(prep.bsg).getroot()
            g = prep.placement.stats
            gx, gy, gz = floats(tree.find("Global/Position"), "x", "y", "z")
            self.assertEqual((gx, gz), (-g["center_x"], -g["center_z"]), name)
            spawn = prep.rebuilt.machine.compute_spawn_height(clearance=0.1, rotation=[0, 0, 0, 1])
            self.assertAlmostEqual(gy, spawn, places=12, msg=name)
            self.assertEqual(floats(tree.find("Global/Rotation"), "x", "y", "z", "w"), (0.0, 0.0, 0.0, 1.0))
            cargo = tree.findall("Blocks/Block")[-1]
            self.assertEqual(cargo.get("id"), "35")
            self.assertEqual(floats(cargo.find("Transform/Position"), "x", "y", "z"),
                             (g["center_x"], g["top_y"] + 3.0, g["center_z"]))
            self.assertEqual(floats(cargo.find("Transform/Rotation"), "x", "y", "z", "w"), L.BALLAST_ROTATION_XYZW)
            self.assertEqual(floats(cargo.find("Transform/Scale"), "x", "y", "z"), (2.5, 2.5, 1.5))
            self.assertEqual(data_params(cargo)["bmt-mass"], ("Single", "5.0"))

    def test_export_is_deterministic(self):
        name = "rocket_orbit_return"
        history = self.paths.v2_root / "control" / "examples" / name / "machine.json"
        again = prepare(history, "transport", "medium", EvalConfig(), self.out / "again", name)
        self.assertEqual(again.manifest["bsg_sha256"], self.prepared[name].manifest["bsg_sha256"])
        self.assertEqual(again.bsg.read_bytes(), self.prepared[name].bsg.read_bytes())


class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.v2_ready()
        cls.out = support.scratch("synthetic_")
        cls.car = support.build_history(support.CAR, cls.out / "car_src", "car")
        cls.rocket = support.build_history(support.ROCKET, cls.out / "rocket_src", "rocket")
        cls.bridge = support.build_history(support.BRIDGE_EAST, cls.out / "bridge_src", "bridge")

    def test_backfill_writes_v1_parameters(self):
        prep = prepare(self.car, "transport", "soft", EvalConfig(), self.out / "car_on", "car")
        tree = ET.parse(prep.bsg).getroot()
        wheels = [b for b in tree.findall("Blocks/Block") if b.get("id") == "2"]
        self.assertEqual(len(wheels), 4)
        for wheel in wheels:
            params = data_params(wheel)
            self.assertEqual(params["flipped"], ("Boolean", "False"))
            for tag, key, text in V1_BLOCK_PARAMS["2"]:
                self.assertEqual(params[key], (tag, text))
        for block in tree.findall("Blocks/Block"):
            if block.get("id") != "2":
                self.assertFalse(set(data_params(block)) & {k for _, k, _ in V1_BLOCK_PARAMS["2"]})
        self.assertEqual(len(prep.manifest["params_written"]), 4 * len(V1_BLOCK_PARAMS["2"]))
        self.assertIsNone(tree.find("Blocks/Block[@id='35']"), "soft transport has no cargo")

    def test_backfill_off_and_water_cannon(self):
        prep = prepare(self.rocket, "lift", "soft", EvalConfig(), self.out / "rocket_on", "rocket")
        cannon = ET.parse(prep.bsg).getroot().find("Blocks/Block[@id='56']")
        self.assertEqual(data_params(cannon)["bmt-strength"], ("Single", "4"))
        self.assertEqual(data_params(cannon)["bmt-hold-to-fire"], ("Boolean", "True"))
        off = prepare(self.rocket, "lift", "soft", replace(EvalConfig(), backfill_params=False),
                      self.out / "rocket_off", "rocket")
        cannon = ET.parse(off.bsg).getroot().find("Blocks/Block[@id='56']")
        self.assertNotIn("bmt-strength", data_params(cannon))
        self.assertEqual(off.manifest["params_written"], [])

    def test_lift_is_not_shifted(self):
        prep = prepare(self.rocket, "lift", "medium", EvalConfig(), self.out / "rocket_lift", "rocket")
        tree = ET.parse(prep.bsg).getroot()
        gx, gy, gz = floats(tree.find("Global/Position"), "x", "y", "z")
        self.assertEqual((gx, gz), (0.0, 0.0))
        self.assertAlmostEqual(gy, prep.placement.spawn_y, places=12)
        self.assertIsNone(tree.find("Blocks/Block[@id='35']"))

    def test_support_rotates_a_bridge_lying_east_west(self):
        prep = prepare(self.bridge, "support", "soft", EvalConfig(), self.out / "bridge_support", "bridge")
        g = prep.placement.stats
        self.assertTrue(prep.placement.rotated)
        tree = ET.parse(prep.bsg).getroot()
        self.assertEqual(floats(tree.find("Global/Rotation"), "x", "y", "z", "w"), L.SUPPORT_ROTATED_XYZW)
        gx, gy, gz = floats(tree.find("Global/Position"), "x", "y", "z")
        self.assertEqual((gx, gz), (g["center_z"], g["center_x"]))
        self.assertEqual(gy, 5.0 - (g["bottom_y"] - 1))
        cargo = tree.find("Blocks/Block[@id='35']")
        self.assertEqual(floats(cargo.find("Transform/Position"), "x", "y", "z"),
                         (g["center_x"], g["top_y"] + 3.0, g["center_z"]))
        self.assertEqual(floats(cargo.find("Transform/Scale"), "x", "y", "z"), (2.5, 2.5, 1.5))

    def test_hard_transport_cargo_scale(self):
        prep = prepare(self.car, "transport", "hard", EvalConfig(), self.out / "car_hard", "car")
        cargo = ET.parse(prep.bsg).getroot().find("Blocks/Block[@id='35']")
        self.assertEqual(floats(cargo.find("Transform/Scale"), "x", "y", "z"), (4.0, 8.0, 1.5))

    def test_machine_state_and_actions(self):
        prep = prepare(self.car, "transport", "soft", EvalConfig(), self.out / "car_state", "car")
        self.assertEqual(sorted(prep.actions), ["4", "5", "6", "7"])
        self.assertEqual(set(prep.actions.values()), {("spinning_forward", "spinning_backward")})
        state = prep.machine_state
        self.assertTrue(state.startswith("Machine Summary: \n"))
        self.assertIn("\nAvailable Actions: \nThe machine has the following powered blocks:\n", state)
        self.assertEqual(state.count("Action: spinning_forward, Key: "), 4)
        self.assertTrue(state.endswith("\nControl Config: \nNo control key has been added to any block yet."))
        self.assertNotIn(str(self.car), state)

    def test_failed_and_unknown_operations_are_refused(self):
        ops = json.loads(self.car.read_text())
        bad = self.out / "bad"
        bad.mkdir()
        (bad / "bad.json").write_text(json.dumps(ops + [{"op": "attach_block_to", "params": {
            "base_block": 1, "face": "f99", "new_block": "Wheel", "note": None}}]))
        with self.assertRaises(RebuildError):
            prepare(bad / "bad.json", "transport", "soft", EvalConfig(), bad / "out1", "bad")
        (bad / "odd.json").write_text(json.dumps(ops + [{"op": "teleport", "params": {}}]))
        with self.assertRaises(RebuildError):
            prepare(bad / "odd.json", "transport", "soft", EvalConfig(), bad / "out2", "odd")


def blocks_by_build_id(bsg: Path, table: list[dict]) -> list[tuple[str, str, list[str]]]:
    """(Build ID, block type, Transform/Data lines) per block in file order, GUIDs
    replaced by Build IDs so BSGs of different histories compare."""
    guid_to_id = {r["guid"]: r["build_id"] or r["legacy_id"] for r in table}
    out = []
    for block in ET.parse(bsg).getroot().findall("Blocks/Block"):
        body = [ET.tostring(child, encoding="unicode").strip() for child in block]
        for guid, bid in guid_to_id.items():
            body = [line.replace(guid, f"<{bid}>") for line in body]
        out.append((guid_to_id[block.get("guid")], block.get("id"), body))
    return out


class StartingBlockOrder(unittest.TestCase):
    """2.0 refresh_block (shift_block, twist_block) moves the block to the end of
    machine.blocks; the Starting Block can end up anywhere but first. 2.0 and
    1.0 to_xml write it first by name, and so must the block table."""

    @classmethod
    def setUpClass(cls):
        support.v2_ready()
        cls.out = support.scratch("start_order_")
        shift = lambda bid, dz: ("shift_block", {"block_id": bid, "shift_real": [0, 0, dz]})
        # A real transport_soft machine flipped two wheels, then shifted the Starting Block away and back.
        car = support.CAR + [("flip_spin", {"block_id": 5})]
        cls.ops_moved = car + [shift(1, 0.5), shift(2, 0.5), shift(2, -0.5), shift(1, -0.5)]
        cls.moved = support.build_history(cls.ops_moved, cls.out / "moved_src", "moved")
        cls.plain = support.build_history(car, cls.out / "plain_src", "plain")
        # A real transport_hard machine did this: shift it while it is the only block.
        cls.early = support.build_history([("start", {}), shift(1, 0.5)] + support.CAR[1:],
                                          cls.out / "early_src", "early")

    def live_machine(self, ops, name):
        """The machine as the MCP holds it at the end of the build session."""
        from buildarena.build import Machine
        machine = Machine(name=name, save_dir=str(self.out / f"{name}_live"), write_full_history=False)
        for op, params in ops:
            getattr(machine, op)(**params)
        return machine

    def test_replay_leaves_the_starting_block_last(self):
        from buildarena.build import Machine
        machine = Machine(name="raw", save_dir=str(self.out / "raw"), write_full_history=False)
        machine.rebuild_from_history(json.loads(self.moved.read_text()))
        self.assertEqual(list(machine.blocks), ["3", "4", "5", "6", "7", "2", "1"])

    def test_shifted_starting_block_is_put_first(self):
        prep = prepare(self.moved, "transport", "soft", EvalConfig(), self.out / "moved", "moved")
        self.assertTrue(prep.manifest["starting_block_moved_first"])
        self.assertEqual([r["build_id"] for r in prep.blocks], ["1", "3", "4", "5", "6", "7", "2"])
        bsg_guids = [b.get("guid") for b in ET.parse(prep.bsg).getroot().findall("Blocks/Block")]
        self.assertEqual(bsg_guids, [r["guid"] for r in prep.blocks])
        from buildarena1.evalkit.judge import v1_block_data
        self.assertEqual(list(v1_block_data(prep.blocks)), ["3", "4", "5", "6", "7", "2", "1"])

    def test_same_order_and_transforms_as_the_live_machine_export(self):
        prep = prepare(self.moved, "transport", "soft", EvalConfig(), self.out / "moved_live", "moved")
        live = ET.fromstring(self.live_machine(self.ops_moved, "moved").to_xml()).findall("Blocks/Block")
        mine = ET.parse(prep.bsg).getroot().findall("Blocks/Block")
        self.assertEqual([b.get("id") for b in live], [b.get("id") for b in mine])
        for a, b in zip(live, mine):
            for tag in ("Position", "Rotation", "Scale"):
                self.assertEqual(a.find(f"Transform/{tag}").attrib, b.find(f"Transform/{tag}").attrib)
            flip_a, flip_b = (x.find("Data/Boolean[@key='flipped']") for x in (a, b))
            self.assertEqual(flip_a is None, flip_b is None)
            if flip_a is not None:
                self.assertEqual(flip_a.text, flip_b.text)
        flips = [b.find("Data/Boolean[@key='flipped']").text for b in mine if b.get("id") == "2"]
        self.assertEqual(sorted(flips), ["False", "False", "False", "True"])   # the flip_spin is there to compare

    def test_cancelling_shifts_give_the_unshifted_machine(self):
        moved = prepare(self.moved, "transport", "soft", EvalConfig(), self.out / "moved_cmp", "m")
        plain = prepare(self.plain, "transport", "soft", EvalConfig(), self.out / "plain_cmp", "m")
        self.assertNotIn("starting_block_moved_first", plain.manifest)
        self.assertEqual(sorted(r["build_id"] for r in moved.blocks), sorted(r["build_id"] for r in plain.blocks))
        by_id = lambda prep: {bid: rest for bid, *rest in blocks_by_build_id(prep.bsg, prep.blocks)}
        self.assertEqual(by_id(moved), by_id(plain))

    def test_starting_block_already_first_is_untouched(self):
        prep = prepare(self.early, "transport", "soft", EvalConfig(), self.out / "early", "early")
        self.assertNotIn("starting_block_moved_first", prep.manifest)
        self.assertEqual([r["build_id"] for r in prep.blocks], ["1", "2", "3", "4", "5", "6", "7"])

    def test_no_or_two_starting_blocks_are_refused(self):
        from buildarena1.evalkit.rebuild import starting_block_first
        block = lambda name: types.SimpleNamespace(name=name)
        for blocks in ({"1": block("Wheel")}, {"1": block("Starting Block"), "2": block("Starting Block")}):
            with self.assertRaises(RebuildError):
                starting_block_first(types.SimpleNamespace(blocks=dict(blocks)))
        machine = types.SimpleNamespace(blocks={"2": block("Wheel"), "1": block("Starting Block"), "3": block("Wheel")})
        self.assertTrue(starting_block_first(machine))
        self.assertEqual(list(machine.blocks), ["1", "2", "3"])
        self.assertFalse(starting_block_first(machine))


def fake_machine(centers):
    blocks = {str(i + 1): types.SimpleNamespace(center_pos=types.SimpleNamespace(virtual=c))
              for i, c in enumerate(centers)}
    return types.SimpleNamespace(blocks=blocks)


class SupportPlacementRule(unittest.TestCase):
    """simulation_support.py:46-72 on hand-made block centres (virtual x, y, z)."""

    def place(self, centers, level="soft"):
        return plan_placement(fake_machine(centers), "support", level, EvalConfig())

    def test_long_in_x_short_in_z_rotates(self):
        p = self.place([(0, 0, 0), (1, 0, 0), (2, 1, 0), (3, 0, 1)])
        self.assertTrue(p.rotated)
        self.assertEqual(p.shift_virtual, (0.25, -1.0, 1.5))
        self.assertEqual(p.global_position(), (0.25, 6.0, 1.5))

    def test_long_in_z_does_not_rotate(self):
        p = self.place([(0, 0, 0), (0, 0, 6), (1, 0, 3)])
        self.assertFalse(p.rotated)
        self.assertEqual(p.rotation_xyzw, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(p.shift_virtual, (-1 / 3, -1.0, -3.0))

    def test_short_both_ways_but_deeper_in_z_does_not_rotate(self):
        p = self.place([(0, 0, 0), (1, 0, 0), (0, 0, 2)])
        self.assertFalse(p.rotated)

    def test_gap_width_depends_on_level(self):
        centers = [(0, 0, 0), (12, 0, 0), (0, 0, 8)]
        self.assertFalse(self.place(centers, "soft").rotated)
        self.assertTrue(self.place(centers, "medium").rotated)


if __name__ == "__main__":
    unittest.main()
