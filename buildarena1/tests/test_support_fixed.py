"""The standby support protocol: one fixed cargo mass per run on
the stand-in banks, and the cargo put 0.1 above the machine when its mass is
fixed in the BSG."""

from __future__ import annotations

import csv
import json
import math
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from . import support
from . import trajgen
from .test_sample import FakeAgent, FakeGame, make_sample, reply_binding
from .test_stepwise import comparable
from buildarena1.evalkit import stepwise
from buildarena1.evalkit.cargo import machine_top_y
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
from buildarena1.evalkit.rebuild import rebuild
from buildarena1.evalkit.sample import RunOutput, evaluate_sample
from buildarena1.evalkit.support_fixed import describe, judge_run, next_mass

FIXED = replace(EvalConfig(), support_protocol="fixed_mass_banks")


def cargo_of(bsg: Path, guid: str) -> ET.Element:
    return next(b for b in ET.parse(bsg).getroot().find("Blocks") if b.get("guid") == guid)


class BridgeGame:
    """Cargo stays on the bridge (at ``deck``) up to ``capacity``, falls (y 1) above it."""

    def __init__(self, capacity: float, deck: float = 4.5):
        self.capacity, self.deck, self.masses, self.plans = capacity, deck, [], []

    def run(self, plan):
        self.plans.append(plan)
        manifest = json.loads((plan.trial_dir.parent / "prepared" / "block_map.json").read_text())
        cargo = next(b for b in manifest["blocks"] if b["legacy_id"] == "Ballast")
        mass = float(next(n for n in cargo_of(plan.bsg, cargo["guid"]).find("Data") if n.get("key") == "bmt-mass").text)
        self.masses.append(mass)
        out = plan.run_dir / "telemetry.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(trajgen.HEADER)
            for seq, t in enumerate(trajgen.telemetry_times(plan.duration)):
                for b in manifest["blocks"]:
                    row = dict.fromkeys(trajgen.HEADER, "")
                    y = 6.0 if b["guid"] != cargo["guid"] else (self.deck if mass <= self.capacity or t < 1.0 else 1.0)
                    row.update(sample_sequence=seq, simulation_time=repr(t), block_guid=b["guid"], x=0, y=y, z=0,
                               machine_integrity=1.0, alive_block_count=len(manifest["blocks"]))
                    writer.writerow([row[h] for h in trajgen.HEADER])
        return RunOutput(out, {})


class Sequence(unittest.TestCase):
    def test_next_mass(self):
        cfg = FIXED
        self.assertEqual(next_mass([], cfg), 154.5)
        self.assertIsNone(next_mass([(154.5, False)], cfg))
        self.assertEqual(next_mass([(154.5, True)], cfg), 1500.0)
        self.assertIsNone(next_mass([(154.5, True), (1500.0, True)], cfg))
        runs = [(154.5, True), (1500.0, False)]
        masses = []
        while (m := next_mass(runs, cfg)) is not None:
            masses.append(m)
            runs.append((m, m <= 600.0))
        self.assertEqual(len(masses), 4)
        self.assertAlmostEqual(masses[0], math.sqrt(154.5 * 1500.0))
        held = max(m for m, h in runs if h)
        dropped = min(m for m, h in runs if not h)
        self.assertLess(held, 600.0)
        self.assertLess(dropped / held, 1.16)                 # about 15 % resolution
        self.assertAlmostEqual(describe(cfg)["metric_resolution_ratio"], (1500 / 154.5) ** (1 / 16), places=4)


class Judge(unittest.TestCase):
    def write(self, heights):
        path = support.scratch("judge_") / "telemetry.csv"
        with open(path, "w", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(["sample_sequence", "simulation_time", "block_guid", "y"])
            for i, (t, y) in enumerate(heights):
                w.writerow([i, t, "CARGO", y])
                w.writerow([i, t, "OTHER", -9])
        return path

    def test_hold_threshold_and_flags(self):
        ok = judge_run(self.write([(0.04, 6.1), (5.0, 3.96), (12.0, 0.0)]), "cargo", 154.5, "soft", FIXED)
        self.assertTrue(ok["held"] and ok["pass"])               # 3.96 prints as 4.0; rows after 10 s ignored
        self.assertEqual(ok["ballast_max_weight_at_threshold"], 154.5)
        drop = judge_run(self.write([(0.04, 6.1), (2.0, 3.9)]), "cargo", 154.5, "soft", FIXED)
        self.assertEqual((drop["held"], drop["pass"], drop["first_below_4_at"]), (False, False, 2.0))
        self.assertEqual(drop["ballast_max_weight_at_threshold"], 0.0)
        high = judge_run(self.write([(0.04, 25.0), (5.0, 24.0)]), "cargo", 154.5, "soft", FIXED)
        self.assertEqual((high["held"], high["pass"], high["initial_height_above_threshold"]), (True, False, True))
        self.assertFalse(judge_run(self.write([(0.04, 6.0), (5.0, 5.8)]), "cargo", 154.5, "soft", FIXED)["hacked"])
        self.assertTrue(judge_run(self.write([(0.04, 6.0), (5.0, 5.8)]), "cargo", 1500.0, "soft", FIXED)["hacked"])


class Protocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()

    def evaluate(self, capacity, cfg=FIXED, how="oneshot", deck=4.5):
        sample = make_sample(support.scratch("sfix_"), "support_soft", "s01", support.BRIDGE_EAST)
        game = BridgeGame(capacity, deck)
        if how == "oneshot":
            result = evaluate_sample(sample, runner=game, paths=self.paths, cfg=cfg, attempt="a1")
            return sample, game, result, None
        steps = [stepwise.prepare(sample, paths=self.paths, cfg=cfg, attempt="a1", runner=game)]
        while steps[-1]["next"] == "run_trial":
            steps.append(stepwise.run_trial_step(sample, "a1", runner=game))
        stepwise.finalize(sample, "a1")
        return sample, game, json.loads((sample / "eval" / "a1" / "result.json").read_text()), steps

    def test_strong_bridge(self):
        sample, game, result, _ = self.evaluate(5000.0)
        self.assertEqual(game.masses, [154.5, 1500.0])
        self.assertEqual((result["success"], result["metric"]["value"]), (True, 1500.0))
        self.assertEqual([p.duration for p in game.plans], [10.0, 10.0])
        self.assertEqual(result["support_protocol"]["name"], "fixed_mass_v1")
        self.assertIn("all at once", result["support_protocol"]["mass_applied"])
        self.assertFalse(result["hacked_pass_any"])

    def test_holding_1500_high_is_hacked_as_in_1_0(self):
        """1.0: cargo never below 4 over the ramp to 1500 with its lowest point above 5."""
        _, game, result, _ = self.evaluate(5000.0, deck=6.5)
        self.assertEqual(game.masses, [154.5, 1500.0])
        self.assertEqual([t["hacked"] for t in result["trials"]], [False, True])
        self.assertEqual((result["success"], result["metric"]["value"], result["hacked_pass_any"]), (True, 154.5, True))

    def test_bisected_bridge(self):
        sample, game, result, _ = self.evaluate(600.0)
        self.assertEqual(len(game.masses), 6)
        self.assertTrue(result["success"])
        self.assertEqual(result["metric"]["value"], max(m for m in game.masses if m <= 600.0))
        self.assertEqual([t["status"] for t in result["trials"]], ["scored"] * 6)

    def test_weak_bridge(self):
        sample, game, result, _ = self.evaluate(100.0)
        self.assertEqual(game.masses, [154.5])
        self.assertEqual((result["success"], result["metric"]["value"]), (False, None))
        self.assertEqual(result["metric_unsuccessful_best"]["value"], 0.0)

    def test_stepwise_follows_next(self):
        _, game_o, one, _ = self.evaluate(600.0)
        _, game_s, steps_result, steps = self.evaluate(600.0, how="steps")
        self.assertEqual(game_o.masses, game_s.masses)
        self.assertEqual([s["next"] for s in steps], ["run_trial"] * 6 + ["finalize"])
        self.assertEqual(steps[0]["duration"], 10.0)
        self.assertEqual([s["mass"] for s in steps[1:]], game_s.masses)
        self.assertEqual(comparable(one)["trials"], comparable(steps_result)["trials"])
        self.assertEqual((one["success"], one["metric"]), (steps_result["success"], steps_result["metric"]))

    def test_summary_states_the_protocol(self):
        from buildarena1.evalkit import summarize
        sample, _, result, _ = self.evaluate(600.0)
        rows = summarize.summarize(summarize.latest_results(sample.parents[3]))
        row = next(r for r in rows if r["cell"] == "support_soft")
        self.assertEqual((row["successes"], row["success_metric_per_sample"]), (1, [result["metric"]["value"]]))
        text = summarize.markdown(rows)
        self.assertIn("support protocol fixed_mass_v1", text)
        self.assertIn("all at once", text)
        self.assertIn("about 15 %", text)

    def test_refused_without_switch(self):
        _, game, result, _ = self.evaluate(600.0, cfg=replace(EvalConfig(), support_protocol="refuse"))
        self.assertEqual((result["status"], game.masses), ("support_level_not_loaded", []))


class CargoDrop(unittest.TestCase):
    def test_cargo_put_above_the_highest_collider(self):
        paths = support.v2_ready()
        prompts = ControllerPrompts(controller_system_prompt(paths.v1_root, "B", "N"), paths.v1_root)
        for drop in (0.1, None):
            cfg = replace(EvalConfig(), cargo_mass_mode="fixed_bsg", cargo_drop_above_top=drop)
            sample = make_sample(support.scratch("drop_"), "transport_medium", "s01", support.CAR)
            evaluate_sample(sample, runner=FakeGame(), paths=paths, cfg=cfg, attempt="a1",
                            control_agent=FakeAgent(reply_binding("spinning_forward")), prompts=prompts)
            manifest = json.loads((sample / "eval" / "a1" / "prepared" / "block_map.json").read_text())
            cargo = next(b for b in manifest["blocks"] if b["legacy_id"] == "Ballast")
            y = float(cargo_of(Path(manifest["bsg"]), cargo["guid"]).find("Transform/Position").get("y"))
            machine = rebuild(next((sample / "machine").glob("*.json")), work_dir=support.scratch("rb_"),
                              machine_name="m").machine
            top = machine_top_y(machine)
            if drop is None:
                self.assertAlmostEqual(y, manifest["placement"]["stats"]["top_y"] + 3.0)
            else:
                self.assertAlmostEqual(y, top + 0.1)
                self.assertAlmostEqual(manifest["machine_top_collider_y"], top)
                self.assertGreater(top, manifest["placement"]["stats"]["top_y"])   # above the top block centre


if __name__ == "__main__":
    unittest.main()
