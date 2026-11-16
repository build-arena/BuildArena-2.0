"""Run conditions (the batch fingerprint) and the summary's support columns."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import support
from .test_sample import FakeGame, make_sample
from buildarena1.evalkit import summarize
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.sample import FINGERPRINT_NAME, FingerprintMismatch, evaluate_sample


class Identified(FakeGame):
    def __init__(self, buildid="24131898"):
        super().__init__()
        self.buildid = buildid

    def identity(self):
        return {"game_buildid": self.buildid, "v2_commit": "083aae1", "cargo_plugin_sha256": "0792915c"}


class Fingerprint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()

    def test_frozen_at_first_trial_and_checked_after(self):
        root = support.scratch("fp_")
        a, b, c = (make_sample(root, "lift_soft", f"s0{i}", support.ROCKET) for i in (1, 2, 3))
        first = evaluate_sample(a, runner=Identified(), paths=self.paths, attempt="a1")
        frozen = json.loads((root / "batchA" / FINGERPRINT_NAME).read_text())
        self.assertEqual(frozen["fingerprint"]["game"]["game_buildid"], "24131898")
        self.assertEqual(first["trials"][0]["run_fingerprint"]["id"], frozen["id"])
        self.assertIn("commit", first["trials"][0]["evalkit_code"])        # read at the trial, not only at prepare
        evaluate_sample(b, runner=Identified(), paths=self.paths, attempt="a1")    # same conditions: runs
        game = Identified(buildid="25434581")
        with self.assertRaises(FingerprintMismatch) as caught:
            evaluate_sample(c, runner=game, paths=self.paths, attempt="a1")
        self.assertIn("game.game_buildid", str(caught.exception))
        self.assertEqual(game.plans, [])                                    # nothing ran
        with self.assertRaises(FingerprintMismatch):                         # a judging config change too
            evaluate_sample(c, runner=Identified(), paths=self.paths, attempt="a2",
                            cfg=EvalConfig(quantize_like_lua=False))

    def test_summary_splits_a_cell_by_condition(self):
        rows = summarize.summarize([self.result("s01", "aaa"), self.result("s02", "aaa"), self.result("s03", "bbb")])
        self.assertEqual([(r["condition"], r["samples"]) for r in rows], [("aaa", 2), ("bbb", 1)])
        self.assertTrue(all(r["cell_split_by_condition"] for r in rows))
        self.assertIn("Cells split because their samples ran under different conditions", summarize.markdown(rows))

    @staticmethod
    def result(sample, fp, *, passed=False, hacked=False, metric=0.0, cell="support_soft"):
        return {"batch": "b", "model": "m", "cell": cell, "sample": sample, "status": "scored",
                "success": passed and not hacked, "metric": {"name": "ballast_max_weight_at_threshold"},
                "trials": [{"trial": 1, "status": "scored", "pass": passed, "hacked": hacked, "metric": metric,
                            "run_fingerprint": {"id": fp}}],
                "build_usage": {"input_tokens": 1, "output_tokens": 1, "requests": 1}, "control_usage": []}


class SupportColumns(unittest.TestCase):
    def test_hacked_pass_counts_in_the_1_0_mean(self):
        r = Fingerprint.result
        rows = summarize.summarize([r("s01", "x", passed=True, hacked=True, metric=1500.0),
                                    r("s02", "x", passed=True, metric=400.0), r("s03", "x")])
        row = rows[0]
        self.assertEqual((row["successes"], row["v1_passes"], row["hacked_pass_samples"]), (1, 2, 1))
        self.assertAlmostEqual(row["success_metric_mean"], 400.0)
        self.assertAlmostEqual(row["metric_mean_fail_as_0"], (1500.0 + 400.0 + 0.0) / 3)
        text = summarize.markdown(rows)
        self.assertIn("in tok (scored attempt)", text)
        self.assertIn("2/3", text)


class StandbyFlags(unittest.TestCase):
    def test_fixed_mass_method_is_flagged(self):
        paths = support.v2_ready()
        from .test_sample import FakeAgent, reply_binding
        from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
        prompts = ControllerPrompts(controller_system_prompt(paths.v1_root, "B", "N"), paths.v1_root)
        sample = make_sample(support.scratch("flag_"), "transport_medium", "s01", support.CAR)
        result = evaluate_sample(sample, runner=FakeGame(), paths=paths, attempt="a1",
                                 cfg=EvalConfig(cargo_mass_mode="fixed_bsg"),
                                 control_agent=FakeAgent(reply_binding("spinning_forward")), prompts=prompts)
        self.assertIn("cargo_mass:fixed_bsg", result["flags"])
        row = summarize.summarize(summarize.latest_results(sample.parents[3]))[0]
        self.assertEqual(row["flagged_samples"][0]["sample"], "s01")


if __name__ == "__main__":
    unittest.main()
