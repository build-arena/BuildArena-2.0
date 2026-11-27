"""The stepwise path (evalkit.stepwise, driven step by step the way a remote
driver calls the command line) gives the same result.json as evaluate_sample on the same fake game
and the same fake control agent; the control agent sees the same messages."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import support
from .test_sample import FakeAgent, FakeGame, make_sample, reply_binding
from .test_run_stage_fixes import SequenceAgent
from buildarena1.evalkit import cli, stepwise, summarize
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
from buildarena1.evalkit.sample import SUPPORT_REFUSED, GameBusy, evaluate_sample

ATTEMPT = "20260101T000000Z"


def drive(sample: Path, game, agent, prompts, paths) -> dict:
    out = stepwise.prepare(sample, paths=paths, attempt=ATTEMPT, runner=game)
    while out["next"] != "done":
        if out["next"] == "controller_input":
            asked = stepwise.controller_input(sample, ATTEMPT, prompts=prompts)
            reply = agent(asked["system"], asked["messages"])
            out = stepwise.parse_reply(sample, ATTEMPT, reply=reply.text, usage=reply.usage)
        elif out["next"] == "run_trial":
            out = stepwise.run_trial_step(sample, ATTEMPT, runner=game)
        elif out["next"] == "finalize":
            stepwise.finalize(sample, ATTEMPT)
            break
        else:
            raise AssertionError(out)
    return json.loads((sample / "eval" / ATTEMPT / "result.json").read_text())


def comparable(result: dict) -> dict:
    # tuples in memory, lists on disk; files of the run (the CargoMass log and its
    # aligned trace) are named under the sample directory, which differs between the two
    text = json.dumps(result, default=str)
    if result.get("sample_dir"):
        text = text.replace(str(result["sample_dir"]), "<sample>")
    result = json.loads(text)
    drop = {"sample_dir", "attempt", "runtime"}
    out = {k: v for k, v in result.items() if k not in drop}
    if "prepared" in out:
        out["prepared"] = {k: v for k, v in out["prepared"].items() if k != "bsg"}
    if "machine_check" in out:   # each copy's MCP BSG has its own uuid4 block GUIDs
        out["machine_check"] = {k: v for k, v in out["machine_check"].items() if k != "mcp_bsg_sha256"}
    return out


class StepwiseMatchesOneShot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()
        cls.prompts = ControllerPrompts(controller_system_prompt(cls.paths.v1_root, "BLOCKS", "NOTE"),
                                        cls.paths.v1_root)
        cls.cases = {"t_ok": ("transport_soft", support.CAR, reply_binding("spinning_forward")),
                     "t_bad": ("transport_soft", support.CAR, "I refuse."),
                     "t_still": ("transport_soft", support.CAR, reply_binding("spinning_backward")),
                     "t_none": ("transport_soft", None, ""),
                     "t_retry": ("transport_soft", support.CAR,
                                 ["no json here", "```json\n{oops\n```", reply_binding("spinning_forward")]),
                     "t_unpowered": ("transport_soft", support.BRIDGE_EAST, reply_binding("spinning_forward")),
                     "support": ("support_soft", support.BRIDGE_EAST, None),
                     "lift_soft": ("lift_soft", support.ROCKET, None),
                     "lift_med": ("lift_medium", support.ROCKET, None)}
        cls.pairs = {}
        for key, (cell, ops, text) in cls.cases.items():
            results, calls = [], []
            for label, how in (("oneshot", "o"), ("steps", "s")):
                root = support.scratch(f"step_{label}_")
                sample = make_sample(root, cell, "s01", ops)
                agent = (SequenceAgent(text) if isinstance(text, list) else FakeAgent(text)) if text is not None else None
                if how == "o":
                    results.append(evaluate_sample(sample, runner=FakeGame(), paths=cls.paths, control_agent=agent,
                                                   prompts=cls.prompts, attempt=ATTEMPT))
                else:
                    results.append(drive(sample, FakeGame(), agent, cls.prompts, cls.paths))
                calls.append(agent.calls if agent else [])
            cls.pairs[key] = (results, calls)

    def test_same_results(self):
        for key, ((oneshot, steps), _) in self.pairs.items():
            self.assertEqual(comparable(oneshot), comparable(steps), key)

    def test_same_agent_messages(self):
        for key, (_, (one, two)) in self.pairs.items():
            self.assertEqual(one, two, key)

    def test_round_counts(self):
        self.assertEqual([t["status"] for t in self.pairs["t_ok"][0][1]["trials"]], ["scored"] * 3)
        self.assertEqual(len(self.pairs["t_still"][0][1]["trials"]), 2)
        self.assertEqual(self.pairs["t_bad"][0][1]["status"], "control_parse_failed")
        self.assertEqual(len(self.pairs["t_bad"][1][1]), 3)
        self.assertEqual(len(self.pairs["t_retry"][0][1]["control_usage"]), 3 + 1 + 1)   # 3 calls in round 1, then 1 per round
        self.assertEqual(self.pairs["t_unpowered"][0][1]["status"], "no_powered_blocks")
        self.assertEqual(self.pairs["t_unpowered"][1][1], [])

    def test_out_of_order_and_refusal(self):
        root = support.scratch("step_order_")
        sample = make_sample(root, "lift_soft", "s01", support.ROCKET)
        stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        with self.assertRaises(stepwise.StepError):
            stepwise.controller_input(sample, ATTEMPT, prompts=self.prompts)

        class NoLevels(FakeGame):
            loads_support_levels = False
        # support is refused only when the config says so (default: the banks of evalkit/terrain.py)
        sample = make_sample(support.scratch("step_nolevel_"), "support_soft", "s01", support.BRIDGE_EAST)
        out = stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=NoLevels(),
                               cfg=EvalConfig(support_protocol="refuse"))
        self.assertEqual((out["status"], out["next"]), (SUPPORT_REFUSED, "done"))
        sample = make_sample(support.scratch("step_banks_"), "support_soft", "s01", support.BRIDGE_EAST)
        out = stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=NoLevels())
        self.assertEqual((out["status"], out["next"]), ("prepared", "run_trial"))

    def test_finalize_after_failed_control_call(self):
        sample = make_sample(support.scratch("step_fail_"), "transport_soft", "s01", support.CAR)
        stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        stepwise.controller_input(sample, ATTEMPT, prompts=self.prompts)
        with self.assertRaises(stepwise.StepError):
            stepwise.finalize(sample, ATTEMPT)
        result = stepwise.finalize(sample, ATTEMPT, reason="HTTP 429")
        self.assertEqual(result["status"], "control_call_failed")
        self.assertFalse(result["success"])

    def test_failed_control_call_usage_is_counted(self):
        root = support.scratch("step_failuse_")
        sample = make_sample(root, "transport_soft", "s01", support.CAR)
        stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        stepwise.controller_input(sample, ATTEMPT, prompts=self.prompts)
        billed = {"input_tokens": 700, "output_tokens": 0, "requests": 2}
        with self.assertRaises(stepwise.StepError):          # usage without the reason it goes with
            stepwise.finalize(sample, ATTEMPT, usage=billed)
        result = stepwise.finalize(sample, ATTEMPT, reason="HTTP 429 after 2 tries", usage=billed)
        self.assertEqual(result["control_usage"], [{**billed, "control_call_failed": "HTTP 429 after 2 tries"}])
        trial = json.loads((sample / "eval" / ATTEMPT / "trial_1" / "trial.json").read_text())
        self.assertEqual(trial["usage"], billed)
        row = summarize.summarize(summarize.latest_results(root))[0]
        self.assertEqual((row["input_tokens"], row["requests"]), (100 + 700, 3 + 2))   # build usage + the failed call

    def test_game_busy_leaves_the_state(self):
        class Busy(FakeGame):
            def __init__(self, busy):
                super().__init__()
                self.busy = busy

            def run(self, plan):
                if self.busy:
                    self.busy -= 1
                    raise GameBusy("lock held")
                return super().run(plan)
        sample = make_sample(support.scratch("step_busy_"), "lift_soft", "s01", support.ROCKET)
        stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        state_path = sample / "eval" / ATTEMPT / "state.json"
        before = state_path.read_text()
        with self.assertRaises(GameBusy):
            stepwise.run_trial_step(sample, ATTEMPT, runner=Busy(1))
        self.assertEqual(state_path.read_text(), before)
        self.assertFalse((sample / "eval" / ATTEMPT / "trial_1" / "run_error.json").exists())
        out = stepwise.run_trial_step(sample, ATTEMPT, runner=Busy(0))     # the same step again, later
        self.assertEqual((out["status"], out["next"]), ("scored", "finalize"))
        trial = json.loads((sample / "eval" / ATTEMPT / "trial_1" / "trial.json").read_text())
        self.assertEqual(trial["run_attempts"], 1)

    def test_run_trial_reentry_does_not_run_again(self):
        """A run-trial call that ended after writing trial.json but before state.json:
        the next call restores the state from the record and runs nothing."""
        for cell, ops, text in (("transport_soft", support.CAR, reply_binding("spinning_forward")),
                                ("lift_soft", support.ROCKET, None)):
            sample = make_sample(support.scratch("step_reenter_"), cell, "s01", ops)
            stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
            if text:
                asked = stepwise.controller_input(sample, ATTEMPT, prompts=self.prompts)
                reply = FakeAgent(text)(asked["system"], asked["messages"])
                stepwise.parse_reply(sample, ATTEMPT, reply=reply.text, usage=reply.usage)
            state_path = sample / "eval" / ATTEMPT / "state.json"
            before = state_path.read_text()
            game = FakeGame()
            first = stepwise.run_trial_step(sample, ATTEMPT, runner=game)
            after = state_path.read_text()
            state_path.write_text(before)                     # the process ended before saving the state
            again = FakeGame()
            second = stepwise.run_trial_step(sample, ATTEMPT, runner=again)
            self.assertEqual(again.plans, [], cell)            # no second game run
            self.assertTrue(second.pop("resumed"))
            self.assertEqual(second, first, cell)
            self.assertEqual(json.loads(state_path.read_text()), json.loads(after), cell)

    def test_run_trial_reentry_with_an_incomplete_record_runs_again(self):
        sample = make_sample(support.scratch("step_reenter_bad_"), "lift_soft", "s01", support.ROCKET)
        stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        tdir = sample / "eval" / ATTEMPT / "trial_1"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "trial.json").write_text('{"trial": 1, "sta')   # cut short while writing
        game = FakeGame()
        out = stepwise.run_trial_step(sample, ATTEMPT, runner=game)
        self.assertEqual((len(game.plans), out["status"], out.get("resumed")), (1, "scored", None))

    def test_incomplete_usage_is_flagged_in_the_summary(self):
        root = support.scratch("step_partial_")
        sample = make_sample(root, "transport_soft", "s01", support.CAR)
        stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        stepwise.controller_input(sample, ATTEMPT, prompts=self.prompts)
        known = {"input_tokens": 300, "output_tokens": 0, "requests": 1, "complete": False}
        stepwise.finalize(sample, ATTEMPT, reason="stream cut after message_start", usage=known)
        row = summarize.summarize(summarize.latest_results(root))[0]
        self.assertEqual((row["input_tokens"], row["requests"]), (100 + 300, 3 + 1))     # the known part is counted
        self.assertEqual(row["usage_incomplete"], [{"sample": "s01", "parts": ["control"]}])
        text = summarize.markdown([row])
        self.assertIn("Usage incomplete", text)
        self.assertIn(">=400", text)


class CommandLine(unittest.TestCase):
    """The summarize subcommand and the game-busy answer of run-trial, through evalkit.cli.main."""

    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()

    def host_file(self, tmp: Path) -> Path:
        host = tmp / "host.json"
        host.write_text(json.dumps({"v1_root": str(self.paths.v1_root), "v2_root": str(self.paths.v2_root),
                                    "collider_dump": str(self.paths.collider_dump),
                                    "besiege_data": str(self.paths.besiege_data), "tasks_dir": str(tmp)}))
        return host

    def call(self, argv: list[str]) -> tuple[int, dict]:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(argv)
        return code, json.loads(buf.getvalue())

    def test_summarize(self):
        root = support.scratch("cli_sum_")
        sample = make_sample(root, "lift_soft", "s01", support.ROCKET)
        evaluate_sample(sample, runner=FakeGame(), paths=self.paths, attempt=ATTEMPT)
        md = root / "summary.md"
        code, out = self.call(["--host", str(self.host_file(root)), "summarize", str(root), "--markdown", str(md)])
        self.assertEqual(code, 0)
        self.assertEqual(out["rows"], json.loads(json.dumps(summarize.summarize(summarize.latest_results(root)))))
        self.assertEqual(out["markdown"], md.read_text())
        self.assertEqual(out["next"], "done")

    def test_next_names_a_subcommand(self):
        """A driver that calls whatever ``next`` says reaches ``done``."""
        root = support.scratch("cli_next_")
        host = self.host_file(root)
        parser_choices = {"prepare", "controller-input", "parse-reply", "run-trial", "finalize"}

        class Host(cli.HostConfig):
            def runner(self):
                return FakeGame()
        real = cli.HostConfig
        cli.HostConfig = Host
        try:
            sample = make_sample(root, "lift_soft", "s01", support.ROCKET)
            code, out = self.call(["--host", str(host), "prepare", str(sample), "--attempt", ATTEMPT])
            seen = []
            while out["next"] != "done":
                self.assertEqual(code, 0, out)
                self.assertIn(out["next"], parser_choices)
                seen.append(out["next"])
                code, out = self.call(["--host", str(host), out["next"], str(sample), "--attempt", ATTEMPT])
            self.assertEqual(seen, ["run-trial", "finalize"])
            car = make_sample(root, "transport_soft", "s02", support.CAR)
            code, out = self.call(["--host", str(host), "prepare", str(car), "--attempt", ATTEMPT])
            self.assertEqual(out["next"], "controller-input")
        finally:
            cli.HostConfig = real

    def test_run_trial_game_busy(self):
        root = support.scratch("cli_busy_")
        sample = make_sample(root, "lift_soft", "s01", support.ROCKET)
        stepwise.prepare(sample, paths=self.paths, attempt=ATTEMPT, runner=FakeGame())
        state = (sample / "eval" / ATTEMPT / "state.json").read_text()

        class Host(cli.HostConfig):
            def runner(self):
                class Busy:
                    def run(self, plan):
                        raise GameBusy("lock held for the whole wait")
                return Busy()
        real = cli.HostConfig
        cli.HostConfig = Host
        try:
            code, out = self.call(["--host", str(self.host_file(root)), "run-trial", str(sample), "--attempt", ATTEMPT])
        finally:
            cli.HostConfig = real
        self.assertEqual(code, 1)
        self.assertEqual((out["game_busy"], out["retry_step"]), (True, "run-trial"))
        self.assertEqual((sample / "eval" / ATTEMPT / "state.json").read_text(), state)


if __name__ == "__main__":
    unittest.main()
