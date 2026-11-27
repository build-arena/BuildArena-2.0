"""How a sample is run when something goes wrong (scoring errors, unparsable
control replies, failed game runs, ambiguous machine files, no powered blocks)
and how trials make a sample; prestart and the support refusal are in
test_sample.py."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from . import support
from .test_sample import FakeAgent, FakeGame, make_sample, reply_binding
from buildarena1.evalkit import sample as S
from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
from buildarena1.evalkit.sample import AgentReply, SampleInfo, combine, evaluate_sample, find_history

ROUND = {"input_tokens": 10, "output_tokens": 5, "requests": 1}


class SequenceAgent:
    """Replies with the given texts in order, then repeats the last one."""

    def __init__(self, texts):
        self.texts, self.calls = list(texts), []

    def __call__(self, system, messages):
        self.calls.append(messages)
        text = self.texts[min(len(self.calls), len(self.texts)) - 1]
        return AgentReply(text, dict(ROUND))


class FlakyGame(FakeGame):
    """Raises on the first ``fail`` runs, then behaves like FakeGame."""

    def __init__(self, fail):
        super().__init__()
        self.fail, self.seen = fail, 0

    def run(self, plan):
        self.seen += 1
        if self.seen <= self.fail:
            plan.run_dir.mkdir(parents=True, exist_ok=False)
            raise RuntimeError(f"game crashed #{self.seen}")
        out = super().run(plan)
        # FakeGame writes under trial_dir/run; move it to the retry's own directory
        if plan.run_name != "run":
            target = plan.run_dir / "telemetry.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            Path(out.telemetry_csv).rename(target)
            return type(out)(target, out.meta)
        return out


def info(cell: str) -> SampleInfo:
    category, _, level = cell.partition("_")
    return SampleInfo(Path("/x"), "b", "m", cell, category, level, "s01")


def scored(n, passed, value, hacked=False, metric="max_transport_distance"):
    return {"trial": n, "status": "scored", "score": {"pass": passed, metric: value, "hacked": hacked}}


class RunStageFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()
        cls.prompts = ControllerPrompts(controller_system_prompt(cls.paths.v1_root, "BLOCKS", "NOTE"),
                                        cls.paths.v1_root)

    def evaluate(self, cell, ops, *, game=None, agent=None):
        sample = make_sample(support.scratch("fix_"), cell, "s01", ops)
        result = evaluate_sample(sample, runner=game or FakeGame(), paths=self.paths, control_agent=agent,
                                 prompts=self.prompts, attempt="a1")
        return sample, result

    # a scoring error must not drop the sample
    def test_score_failure_keeps_other_trials(self):
        real = S.score_trial
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("no tracked rows")
            return real(*args, **kwargs)

        agent = FakeAgent(reply_binding("spinning_forward"))
        with mock.patch.object(S, "score_trial", side_effect=flaky):
            sample, result = self.evaluate("transport_soft", support.CAR, agent=agent)
        self.assertEqual([t["status"] for t in result["trials"]], ["scored", "score_failed"])
        self.assertEqual((result["status"], result["success"]), ("scored", True))
        self.assertTrue((sample / "eval" / "a1" / "trial_2" / "score_error.json").is_file())
        self.assertTrue((sample / "eval" / "a1" / "result.json").is_file())

    # retry an unparsable control reply with the same messages, at most 3 calls
    def test_parse_retry(self):
        agent = SequenceAgent(["no json", "```json\n{bad\n```", reply_binding("spinning_forward")])
        sample, result = self.evaluate("transport_soft", support.CAR, agent=agent)
        self.assertEqual(result["trials"][0]["status"], "scored")
        self.assertEqual(agent.calls[0], agent.calls[1])
        self.assertEqual(agent.calls[1], agent.calls[2])
        self.assertEqual(len(result["control_usage"]), 5)            # 3 calls in round 1, one in rounds 2 and 3
        record = json.loads((sample / "eval" / "a1" / "trial_1" / "control_round.json").read_text())
        self.assertEqual([a["parse_error"] is None for a in record["attempts"]], [False, False, True])

    def test_validation_error_is_not_retried(self):
        body = {"control_config": [], "control_sequence": [{"time": 0, "key": "Alpha1", "hold_for": 1}]}
        agent = FakeAgent("```json\n" + json.dumps(body) + "\n```")
        _, result = self.evaluate("transport_soft", support.CAR, agent=agent)
        self.assertEqual(result["status"], "control_invalid")
        self.assertEqual(len(agent.calls), 1)

    # retry a failed game run up to 3 runs in all
    def test_run_retry(self):
        game = FlakyGame(fail=2)
        sample, result = self.evaluate("lift_soft", support.ROCKET, game=game)
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["trials"][0]["run_attempts"], 3)
        errors = json.loads((sample / "eval" / "a1" / "trial_1" / "run_errors.json").read_text())
        self.assertEqual([e["attempt"] for e in errors], [1, 2])
        self.assertEqual([Path(p.run_dir).name for p in game.plans], ["run_retry3"])

    # only the json named like the .bsg is the history
    def test_find_history_ignores_manifest(self):
        sample = make_sample(support.scratch("fix_hist_"), "lift_soft", "s01", support.ROCKET)
        machine = sample / "machine"
        (machine / "manifest.json").write_text("{}")
        (machine / "lift_soft_s01_full.json").write_text("[]")
        self.assertEqual(find_history(sample), machine / "lift_soft_s01.json")
        (machine / "lift_soft_s01.bsg").unlink()
        self.assertIsNone(find_history(sample))

    # no powered blocks -> no control call
    def test_no_powered_blocks(self):
        agent = FakeAgent(reply_binding("spinning_forward"))
        _, result = self.evaluate("transport_soft", support.BRIDGE_EAST, agent=agent)
        self.assertEqual((result["status"], result["success"]), ("no_powered_blocks", False))
        self.assertEqual(agent.calls, [])
        self.assertEqual(result["control_usage"], [])

    # how trials make a sample
    def test_combine_counts_only_clean_passes(self):
        r = combine(info("transport_soft"), [scored(1, False, 250.0, hacked=True), scored(2, True, 11.0)])
        self.assertEqual((r["success"], r["metric"]["value"], r["metric_unsuccessful_best"]["value"]), (True, 11.0, None))
        self.assertFalse(r["hacked_pass_any"])
        self.assertTrue(r["hacked_any"])
        r = combine(info("transport_soft"), [scored(1, True, 30.0, hacked=True), scored(2, False, 4.0)])
        self.assertEqual((r["success"], r["metric"]["value"], r["metric_unsuccessful_best"]["value"]), (False, None, 30.0))
        self.assertTrue(r["hacked_pass_any"])
        r = combine(info("transport_soft"), [scored(1, False, 3.0), {"trial": 2, "status": "run_failed"}])
        self.assertEqual((r["success"], r["metric"]["value"], r["metric_unsuccessful_best"]["value"]), (False, None, 3.0))


if __name__ == "__main__":
    unittest.main()
