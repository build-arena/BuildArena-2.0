"""Standby cargo-mass switches, support banks and the code-version record:
the cargo mass sent past the slider range, support banks made of Ballast
blocks, and evalkit.version reading DEPLOYED.json."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from . import support
from .test_sample import FakeGame, make_sample
from buildarena1.evalkit import legacy as L
from buildarena1.evalkit import terrain, version
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.control import write_python_controller
from buildarena1.evalkit.sample import SUPPORT_REFUSED, evaluate_sample


class Slider:
    def __init__(self, guid):
        self.index, self.block_guid_lower, self.name, self.minimum, self.maximum = 7, guid, "mass", 0.2, 3.0


class MassClient:
    """Enough of ControllerClient for the runtime controller's cargo path."""
    last = None

    def __init__(self):
        self.times, self.i, self.sent, self.clipped = [k * 0.04 for k in range(1, 60)], 0, [], []
        self._block_guids = {0: "x"}
        self.sliders = [Slider("gb")]
        MassClient.last = self

    @classmethod
    def from_environment(cls, poll_interval=0.05):
        return cls()

    def arm(self): pass
    def close(self): pass
    def _sample(self): return types.SimpleNamespace(simulation_time=self.times[self.i])
    def wait_until_running(self, timeout=120.0): return self._sample()

    def next_sample(self, timeout=2.0):
        self.i += 1
        return self._sample()

    def send_channels(self, channels): return 1

    def send_sliders(self, sliders):
        self.clipped.append(min(max(sliders[0][2], 0.2), 3.0))
        return 2

    def _send_merged(self, *, sliders, keepalive):
        self.sent.append(sliders[0])
        return 3


def run_controller(out: Path) -> list[dict]:
    spec = importlib.util.spec_from_file_location("ctl", out / "controller.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sdk = types.ModuleType("controller_sdk"); sdk.ControllerClient = MassClient
    proto = types.ModuleType("controller_sdk.protocol"); proto.ENV_RUN_DIR = "FAKE_RUN_DIR"
    saved = {k: sys.modules.get(k) for k in ("controller_sdk", "controller_sdk.protocol")}
    sys.modules.update({"controller_sdk": sdk, "controller_sdk.protocol": proto})
    os.environ["FAKE_RUN_DIR"] = str(out)
    try:
        assert module.main() == 0
    finally:
        for k, v in saved.items():
            sys.modules.pop(k, None) if v is None else sys.modules.__setitem__(k, v)
    return [json.loads(l) for l in (out / "controller_trace.jsonl").read_text().splitlines()]


class CargoMass(unittest.TestCase):
    def test_unclipped_sends_the_ramp(self):
        out = support.scratch("mass_")
        write_python_controller(out, duration=2.0, tick_s=0.1, intervals=[], guid_of={}, ballast_guid="gb",
                                ramp=L.SUPPORT_RAMP, mass_unclipped=True)
        self.assertTrue(json.loads((out / "schedule.json").read_text())["ballast"]["unclipped"])
        trace = run_controller(out)
        client = MassClient.last
        self.assertEqual(client.clipped, [])
        self.assertTrue(all(index == 7 for index, _ in client.sent))
        self.assertAlmostEqual(trace[-1]["mass"], L.SUPPORT_RAMP.mass_at(trace[-1]["t"]))
        self.assertGreater(trace[-1]["mass"], 3.0)
        self.assertEqual(trace[-1]["mass"], trace[-1]["mass_ramp"])

    def test_default_clips_and_records_the_clipped_value(self):
        out = support.scratch("mass_")
        write_python_controller(out, duration=2.0, tick_s=0.1, intervals=[], guid_of={}, ballast_guid="gb",
                                ramp=L.SUPPORT_RAMP)
        trace = run_controller(out)
        self.assertEqual(MassClient.last.sent, [])
        self.assertEqual({r["mass"] for r in trace}, {3.0})
        self.assertGreater(trace[-1]["mass_ramp"], 3.0)


class CargoInBsg(unittest.TestCase):
    def test_transport_cargo_mass_written_and_not_sent(self):
        from .test_sample import FakeAgent, reply_binding
        from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
        paths = support.v2_ready()
        prompts = ControllerPrompts(controller_system_prompt(paths.v1_root, "B", "N"), paths.v1_root)
        sample = make_sample(support.scratch("cargo_"), "transport_medium", "s01", support.CAR)
        result = evaluate_sample(sample, runner=FakeGame(), paths=paths, attempt="a1",
                                 cfg=replace(EvalConfig(), cargo_mass_mode="fixed_bsg"),
                                 control_agent=FakeAgent(reply_binding("spinning_forward")), prompts=prompts)
        self.assertEqual((result["status"], result["cargo_mass_in_bsg"]), ("scored", 50.0))
        out = sample / "eval" / "a1"
        manifest = json.loads((out / "prepared" / "block_map.json").read_text())
        cargo = next(b for b in manifest["blocks"] if b["legacy_id"] == "Ballast")
        block = next(b for b in ET.parse(manifest["bsg"]).getroot().find("Blocks") if b.get("guid") == cargo["guid"])
        self.assertEqual(float(next(n for n in block.find("Data") if n.get("key") == "bmt-mass").text), 50.0)
        schedule = json.loads((out / "trial_1" / "schedule.json").read_text())
        self.assertEqual(schedule["ballast"], {"guid": cargo["guid"], "fixed_mass": 50.0})
        rows = (out / "trial_1" / "legacy_rows.csv").read_text().splitlines()
        self.assertTrue(any(r.endswith("ID_Ballast, 50.0") or r.endswith("ID_Ballast,50.0") for r in rows))

    def test_runtime_records_fixed_mass_without_sending(self):
        out = support.scratch("mass_")
        write_python_controller(out, duration=1.0, tick_s=0.1, intervals=[], guid_of={}, ballast_guid="gb",
                                ramp=L.TRANSPORT_RAMP, fixed_mass=50.0)
        trace = run_controller(out)
        self.assertEqual((MassClient.last.sent, MassClient.last.clipped), ([], []))
        self.assertEqual({r["mass"] for r in trace}, {50.0})


class Banks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()

    def test_blv_geometry(self):
        for level, inner in (("soft", 2.5), ("medium", 5.0), ("hard", 10.0)):
            objs = terrain.blv_objects(self.paths.v1_root / "asset" / f"support_{level}.blv")
            self.assertEqual(sorted(abs(o["position"][2]) - o["scale"][2] / 2 for o in objs), [inner, inner])
            self.assertTrue(all(o["scale"] == (30.0, 5.0, 20.0) for o in objs))

    def test_banks_stay_in_world_when_the_machine_is_rotated(self):
        objs = terrain.blv_objects(self.paths.v1_root / "asset" / "support_soft.blv")
        g_pos, g_rot = (1.0, 5.0, -2.0), L.SUPPORT_ROTATED_XYZW
        for row, obj in zip(terrain.bank_blocks(objs, global_position=g_pos, global_rotation=g_rot, mass=1e5,
                                                lift=0.0, guid_seed="s"), objs):
            world = [g_pos[k] + v for k, v in enumerate(terrain._qrot(g_rot, row["local_position"]))]
            for a, b in zip(world, obj["position"]):
                self.assertAlmostEqual(a, b, places=9)
            q = terrain._qmul(g_rot, row["local_rotation"])
            self.assertTrue(all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(q, L.BALLAST_ROTATION_XYZW)))
            # scaled collider in world: 30 east-west, 20 north-south, 5 up
            sx, sy, sz = row["scale"]
            axes = [terrain._qrot(L.BALLAST_ROTATION_XYZW, v) for v in ((0.95 * sx, 0, 0), (0, 0.95 * sy, 0), (0, 0, sz))]
            self.assertEqual([round(max(abs(c) for c in a), 6) for a in axes], [30.0, 20.0, 5.0])
            self.assertAlmostEqual(axes[2][1], 5.0)

    def test_support_runs_on_banks_and_says_so(self):
        cfg = replace(EvalConfig(), support_protocol="fixed_mass_banks")
        sample = make_sample(support.scratch("banks_"), "support_soft", "s01", support.BRIDGE_EAST)

        class NoLevels(FakeGame):
            loads_support_levels = False
        result = evaluate_sample(sample, runner=NoLevels(), paths=self.paths, cfg=cfg, attempt="a1")
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["support_terrain"]["source"], "ballast_banks")
        self.assertIn("support_terrain:ballast_banks", result["flags"])
        self.assertEqual(result["support_protocol"]["name"], "fixed_mass_v1")
        prepared = sample / "eval" / "a1" / "prepared"
        manifest = json.loads((prepared / "block_map.json").read_text())
        bank_guids = {b["guid"] for b in manifest["terrain"]["banks"]}
        self.assertFalse(bank_guids & {b["guid"] for b in manifest["blocks"]})
        tree = ET.parse(manifest["bsg"]).getroot()
        found = [b for b in tree.find("Blocks") if b.get("guid") in bank_guids]
        self.assertEqual([b.get("id") for b in found], ["35", "35"])
        self.assertEqual(manifest["bsg_sha256"], hashlib.sha256(Path(manifest["bsg"]).read_bytes()).hexdigest())
        refused = evaluate_sample(make_sample(support.scratch("banks_"), "support_soft", "s01", support.BRIDGE_EAST),
                                  runner=NoLevels(), paths=self.paths, attempt="a1",
                                  cfg=replace(EvalConfig(), support_protocol="refuse"))
        self.assertEqual(refused["status"], SUPPORT_REFUSED)


class Version(unittest.TestCase):
    def test_deployed_manifest(self):
        root = support.scratch("deployed_")
        (root / version.CODE_DIR).mkdir(parents=True)
        code = root / version.CODE_DIR / "a.py"
        code.write_text("x = 1\n")
        (root / version.MANIFEST).write_text(json.dumps({
            "commit": "abc", "dirty": False, "deployed_at": "t",
            "files": {f"{version.CODE_DIR}/a.py": version.sha256_file(code)}}))
        got = version.code_version(root)
        self.assertEqual((got["source"], got["commit"], got["files_match"]), ("deployed", "abc", True))
        code.write_text("x = 2\n")
        (root / version.CODE_DIR / "b.py").write_text("")
        got = version.code_version(root)
        self.assertEqual((got["files_match"], got["changed_files"], got["unlisted_files"]),
                         (False, [f"{version.CODE_DIR}/a.py"], [f"{version.CODE_DIR}/b.py"]))

    def test_result_records_code_version(self):
        paths = support.v2_ready()
        sample = make_sample(support.scratch("ver_"), "lift_soft", "s01", support.ROCKET)
        result = evaluate_sample(sample, runner=FakeGame(), paths=paths, attempt="a1")
        self.assertIn("evalkit_code", result)
        self.assertIn(result["evalkit_code"]["source"], ("deployed", "git", "unknown"))


if __name__ == "__main__":
    unittest.main()
