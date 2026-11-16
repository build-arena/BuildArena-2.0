"""Lift success under the default height rule and the original 1.0 rule."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from . import trajgen
from buildarena1.evalkit import cli, stepwise
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.judge import score_trial
from buildarena1.evalkit.sample import evaluate_sample

IDENT = {"model_name": "model", "machine_id": "machine", "round_suffix": "_1",
         "machine_bsg_path": "", "machine_json_path": ""}
NAMES = ["Starting Block", "Water Cannon", "Water Cannon", "Torch"]
TIMES = [0.1, 2.0, 3.0, 5.0, 7.0, 8.0, 10.0, 12.0, 30.0]
EARLY_PEAK = [1.0, 1.0, 200.0, 400.0, 300.0, 250.0, 150.0, 50.0, 1.0]


class LiftSuccessRule(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="lift_rule_")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def score_both(self, level, heights, *, heated=True):
        height_at = dict(zip(TIMES, heights))
        scenario = trajgen.Scenario(
            "lift", level, NAMES,
            lambda lid, t: (0.0, height_at[t], 0.0, heated and lid in ("2", "3")),
            times=TIMES,
        )
        telemetry = self.root / "telemetry.csv"
        trajgen.write(scenario, telemetry)
        results, rows = [], []
        for cfg in (EvalConfig(), EvalConfig(lift_success_rule="ba1")):
            score, kept = score_trial(telemetry, scenario.blocks(), "lift", level, IDENT, cfg)
            self.assertEqual(score["v2"]["lift_success_rule"], cfg.lift_success_rule)
            results.append({k: v for k, v in score.items() if k != "v2"})
            rows.append(kept)
        self.assertEqual(rows[0], rows[1])
        return results

    def test_peak_before_seven_seconds_passes_by_default(self):
        for level in ("medium", "hard"):
            with self.subTest(level=level):
                height, ba1 = self.score_both(level, EARLY_PEAK)
                self.assertTrue(height["pass"])
                self.assertFalse(ba1["pass"])
                self.assertFalse(height["hacked"])
                self.assertEqual(height["water_cannon_active"], 2)
                self.assertGreater(height["effective_propulsion_force"], 1.0)
                self.assertEqual(height["max_height"], 400.0)
                self.assertEqual(height["elevation_change"], -150.0)
                self.assertEqual({**height, "pass": False}, ba1)

    def test_peak_below_thirty_fails_both_rules(self):
        for level in ("medium", "hard"):
            with self.subTest(level=level):
                height, ba1 = self.score_both(level, [1.0, 1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 29.9, 1.0])
                self.assertFalse(height["pass"])
                self.assertEqual(height["max_height"], 29.9)
                self.assertGreater(height["elevation_change"], 5.0)
                self.assertEqual(height, ba1)

    def test_peak_at_thirty_fails_both_rules(self):
        for level in ("medium", "hard"):
            with self.subTest(level=level):
                height, ba1 = self.score_both(level, [1.0, 1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 1.0])
                self.assertFalse(height["pass"])
                self.assertEqual(height["max_height"], 30.0)
                self.assertEqual(height, ba1)

    def test_elevation_change_exactly_five_fails_only_ba1(self):
        for level in ("medium", "hard"):
            with self.subTest(level=level):
                height, ba1 = self.score_both(level, [1.0, 1.0, 30.0, 40.0, 40.0, 45.0, 45.0, 45.0, 1.0])
                self.assertEqual(height["elevation_change"], 5.0)
                self.assertTrue(height["pass"])
                self.assertFalse(ba1["pass"])

    def test_pass_with_insufficient_thrust_is_still_hacked(self):
        for level in ("medium", "hard"):
            with self.subTest(level=level):
                height, ba1 = self.score_both(level, EARLY_PEAK, heated=False)
                self.assertLess(height["effective_propulsion_force"], 1.0)
                self.assertTrue(height["pass"])
                self.assertTrue(height["hacked"])
                self.assertFalse(ba1["pass"])
                self.assertFalse(ba1["hacked"])
                # A trajectory passing both criteria still gets the same hacked flag.
                height, ba1 = self.score_both(level, [1.0, 1.0, 5.0, 10.0, 15.0, 20.0, 35.0, 40.0, 1.0],
                                             heated=False)
                self.assertTrue(height["pass"])
                self.assertTrue(height["hacked"])
                self.assertEqual(height, ba1)

    def test_level_one_still_uses_thrust_to_weight(self):
        for heated in (True, False):
            with self.subTest(heated=heated):
                heights = [1.0] * len(TIMES) if heated else EARLY_PEAK
                height, ba1 = self.score_both("soft", heights, heated=heated)
                self.assertEqual(height["pass"], heated)
                self.assertEqual(height, ba1)

    def test_rule_is_read_from_config_and_written_to_results(self):
        config_file = self.root / "config.json"
        config_file.write_text(json.dumps({"lift_success_rule": "ba1"}), encoding="utf-8")
        for rule, cfg in (("height", cli._config(None)), ("ba1", cli._config(str(config_file)))):
            for mode in ("oneshot", "steps"):
                with self.subTest(rule=rule, mode=mode):
                    sample = self.root / "batch" / "model" / "lift_medium" / f"{rule}_{mode}"
                    sample.mkdir(parents=True)
                    # No machine: these paths write a result without game data or a rebuild.
                    if mode == "oneshot":
                        evaluate_sample(sample, runner=None, paths=None, cfg=cfg, attempt="a1")
                    else:
                        stepwise.prepare(sample, paths=None, cfg=cfg, attempt="a1")
                        state = json.loads((sample / "eval" / "a1" / "state.json").read_text())
                        self.assertEqual(stepwise._cfg(state).lift_success_rule, rule)
                    result = json.loads((sample / "eval" / "a1" / "result.json").read_text())
                    self.assertEqual(result["status"], "no_machine")
                    self.assertEqual(result["config"]["lift_success_rule"], rule)


if __name__ == "__main__":
    unittest.main()
