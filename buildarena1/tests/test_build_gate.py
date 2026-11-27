"""Which build checks keep a sample from
evaluation, which ones only flag it, the final-reply fallback for the control
agent, and the summary listing flagged samples."""

from __future__ import annotations

import json
import unittest

from . import support
from .test_sample import FINAL_REPLY, FakeAgent, FakeGame, make_sample, reply_binding
from buildarena1.evalkit import stepwise, summarize
from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
from buildarena1.evalkit.sample import evaluate_sample

ATTEMPT = "a1"


class BuildGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()
        cls.prompts = ControllerPrompts(controller_system_prompt(cls.paths.v1_root, "BLOCKS", "NOTE"),
                                        cls.paths.v1_root)
        cls.root = support.scratch("gate_")

    def run_one(self, cell, ops, name, **kw):
        sample = make_sample(self.root, cell, name, ops, **kw)
        game, agent = FakeGame(), FakeAgent(reply_binding("spinning_forward"))
        result = evaluate_sample(sample, runner=game, paths=self.paths, control_agent=agent,
                                 prompts=self.prompts, attempt=ATTEMPT)
        return result, game, agent

    def test_gate_check_blocks(self):
        result, game, _ = self.run_one("lift_soft", support.ROCKET, "s01", checks={"not_smoke": False})
        self.assertEqual(result["status"], "build_invalid")
        self.assertEqual(result["build_gate_failed"], ["not_smoke"])
        self.assertEqual(game.plans, [])

    def test_flag_checks_do_not_block(self):
        result, game, _ = self.run_one("lift_soft", support.ROCKET, "s02", session_status="context_limit",
                                       checks={"close_machine_lifespan_succeeded": False,
                                               "no_sample_identity_in_tool_results": False})
        self.assertEqual(result["status"], "scored")
        self.assertEqual(len(game.plans), 1)
        self.assertEqual(result["build_session_status"], "context_limit")
        self.assertEqual(set(result["flags"]), {"build_session:context_limit",
                                                 "build:close_machine_lifespan_succeeded",
                                                 "build:no_sample_identity_in_tool_results"})

    def test_reply_without_sections_is_the_description(self):
        text = "I built a four-wheel car; hold the wheels forward to drive north-east."
        result, _, agent = self.run_one("transport_soft", support.CAR, "s03", final_reply=text,
                                        checks={"final_reply_sections_present": False})
        self.assertEqual(result["status"], "scored")
        self.assertIn("final_reply_sections_missing", result["flags"])
        self.assertIn(text, agent.calls[0][0]["content"])
        result, _, agent = self.run_one("transport_soft", support.CAR, "s04", final_reply=None)
        self.assertEqual(result["status"], "scored")
        self.assertIn("final_reply_missing", result["flags"])
        self.assertIn("The plan of the machine structure: \n\n", agent.calls[0][0]["content"])

    def test_stepwise_gate_and_flags(self):
        sample = make_sample(support.scratch("gate_step_"), "lift_soft", "s01", support.ROCKET,
                             checks={"tools_offered_ok": False})
        out = stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        self.assertEqual((out["status"], out["next"]), ("build_invalid", "done"))
        sample = make_sample(support.scratch("gate_step_"), "transport_soft", "s01", support.CAR,
                             session_status="max_turns", final_reply="no sections here",
                             checks={"close_machine_lifespan_succeeded": False})
        out = stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        self.assertEqual(out["next"], "controller_input")
        self.assertIn("build_session:max_turns", out["flags"])
        self.assertIn("final_reply_sections_missing", out["flags"])
        asked = stepwise.controller_input(sample, ATTEMPT, prompts=self.prompts)
        self.assertIn("no sections here", asked["messages"][0]["content"])

    def test_summary_lists_flagged_samples(self):
        root = support.scratch("gate_sum_")
        make_sample(root, "lift_soft", "s01", support.ROCKET)
        flagged = make_sample(root, "lift_soft", "s02", support.ROCKET, session_status="context_limit",
                              checks={"close_machine_lifespan_succeeded": False})
        for s in (root / "batchA" / "modelX" / "lift_soft").iterdir():
            evaluate_sample(s, runner=FakeGame(), paths=self.paths, attempt=ATTEMPT)
        rows = summarize.summarize(summarize.latest_results(root))
        self.assertEqual([f["sample"] for f in rows[0]["flagged_samples"]], [flagged.name])
        self.assertEqual(rows[0]["flagged_samples"][0]["build_session_status"], "context_limit")
        text = summarize.markdown(rows)
        self.assertIn("Evaluated with flags:", text)
        self.assertIn("modelX/lift_soft/s02: build_session:context_limit, build:close_machine_lifespan_succeeded", text)

    def test_incomplete_attempt_counts_every_control_call(self):
        root = support.scratch("gate_usage_")
        sample = make_sample(root, "transport_soft", "s01", support.CAR)
        trial = sample / "eval" / ATTEMPT / "trial_1"
        trial.mkdir(parents=True)
        usage = {"input_tokens": 10, "output_tokens": 5, "requests": 1}
        (trial / "control_round.json").write_text(json.dumps(
            {"usage": usage, "attempts": [{"usage": usage}, {"usage": usage}, {"usage": usage}]}))
        rows = summarize.summarize(summarize.latest_results(root))
        self.assertEqual(rows[0]["requests"], 3 + 3)   # build usage.json 3 plus three control calls


class MissingSample(unittest.TestCase):
    """A sample path that does not exist is refused before anything is written
    (it must not become an attempt with status no_machine)."""

    def test_prepare_refuses_a_missing_sample(self):
        root = support.scratch("missing_")
        sample = root / "batch" / "model" / "lift_soft" / "s01"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            stepwise.prepare(sample, paths=None, attempt=ATTEMPT)
        self.assertFalse((root / "batch").exists())
        with self.assertRaisesRegex(ValueError, "does not exist"):
            evaluate_sample(sample, runner=None, paths=None, attempt=ATTEMPT)
        self.assertFalse((root / "batch").exists())


if __name__ == "__main__":
    unittest.main()
