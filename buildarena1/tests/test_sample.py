"""evaluate_sample end to end with a fake game and a fake control agent,
then the summary over the runs root. Also that scoring is deterministic."""

from __future__ import annotations

import csv
import json
import random
import shutil
import unittest
from dataclasses import replace
from pathlib import Path

from . import support
from . import trajgen
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.legacy import MassRamp
from buildarena1.evalkit.judge import score_trial
from buildarena1.evalkit.sample import FLAG_CHECKS, GATE_CHECKS, SUPPORT_REFUSED, AgentReply, GameNotConnected, RunOutput, RunPlan, evaluate_sample
from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
from buildarena1.evalkit import summarize

USAGE = {"input_tokens": 100, "output_tokens": 50, "requests": 3}
ROUND_USAGE = {"input_tokens": 10, "output_tokens": 5, "requests": 1}
FINAL_REPLY = "<functionality>Four wheels push the body.</functionality>\n<motion_control>Hold Alpha1.</motion_control>"


def reply_binding(action: str) -> str:
    config = [{"key": "Alpha1", "action": action, "block_id": i} for i in (4, 5, 6, 7)]
    body = {"control_design": "x", "control_config": config,
            "control_sequence": [{"motion_action": "go", "time": 0.5, "key": "Alpha1", "hold_for": 25}]}
    return "```json\n" + json.dumps(body) + "\n```"


class FakeAgent:
    def __init__(self, text):
        self.text, self.calls = text, []

    def __call__(self, system, messages):
        self.calls.append(messages)
        return AgentReply(self.text, dict(ROUND_USAGE))


class FakeGame:
    """Writes a telemetry.csv the prepared machine could plausibly produce."""

    loads_support_levels = True   # lets the support cell through the refusal gate

    def __init__(self):
        self.plans = []

    def run(self, plan: RunPlan) -> RunOutput:
        self.plans.append(plan)
        manifest_path = plan.bsg.parent / "block_map.json"
        if not manifest_path.is_file():   # a per-run BSG (support fixed-mass runs) lives in the trial dir
            manifest_path = plan.trial_dir.parent / "prepared" / "block_map.json"
        manifest = json.loads(manifest_path.read_text())
        category, blocks = manifest["category"], manifest["blocks"]
        if plan.controller.suffix == ".json":
            timeline = json.loads(plan.controller.read_text())
            fire = min(e["time"] for e in timeline["events"] if e["value"] > 0)
            height = lambda t: 1.0 + max(0.0, t - fire) * 4.0
            forward = lambda t: False
        else:
            schedule = json.loads(plan.controller.with_name("schedule.json").read_text())
            spans = [(c["start"], c["end"]) for c in schedule["channels"] if c["channel"] == "ForwardKey"]
            forward = lambda t: any(s <= t < e for s, e in spans)
            height = lambda t: 1.0
        out = plan.trial_dir / "run" / "telemetry.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(trajgen.HEADER)
            x = 0.0
            for seq, t in enumerate(trajgen.telemetry_times(plan.duration)):
                x += 0.5 * 0.04 if forward(t) else 0.0
                for i, b in enumerate(blocks):
                    row = dict.fromkeys(trajgen.HEADER, "")
                    # 4.5 holds above the 4.0 threshold without the "min height > 5" hacked flag
                    y = 4.5 if (category == "support" and b["legacy_id"] == "Ballast") else height(t) + 0.01 * i
                    row.update(sample_sequence=seq, simulation_time=repr(t), block_guid=b["guid"], x=x + 0.01 * i,
                               y=y, z=0.0, fire_burning=1 if b["name_v2"] == "Water Cannon" else 0, fire_valid=1,
                               machine_integrity=1.0, alive_block_count=len(blocks))
                    writer.writerow([row[h] for h in trajgen.HEADER])
        if plan.controller.suffix == ".py" and schedule["ballast"] is not None:
            # What evalkit's runtime controller leaves in the run dir: one send per 0.1 s.
            ballast = schedule["ballast"]
            if "fixed_mass" in ballast:   # cargo mass written into the BSG, nothing sent
                ramp = MassRamp(mass_min=ballast["fixed_mass"], mass_max=ballast["fixed_mass"], ramp_duration=1.0)
            else:
                ramp = MassRamp(**{k: ballast[k] for k in ("mass_min", "mass_max", "ramp_duration", "ramp_start",
                                                           "clamp_low", "cap")})
            with open(out.parent / "controller_trace.jsonl", "w") as trace:
                due = 0.0
                for t in trajgen.telemetry_times(plan.duration):
                    if t + 1e-4 >= due:
                        trace.write(json.dumps({"t": t, "held": [], "mass": ramp.mass_at(t)}) + "\n")
                        due = (int(t / 0.1 + 1e-4) + 1) * 0.1
        meta = {"game_build": "fake"}
        if plan.cargo_curve is not None:
            # what the CargoMass plugin leaves: one {"frame", "t", "mass", "y"} line per physics
            # frame (t = frame / 100); y is the cargo's height, the same as telemetry's (the
            # fake cargo does not move, so the clock alignment finds offset 0 and flags it ambiguous)
            c = plan.cargo_curve
            ramp = MassRamp(mass_min=c["mass_min"], mass_max=c["mass_max"], ramp_duration=c["ramp_duration"],
                            ramp_start=c["ramp_start"], clamp_low=c["clamp_low"])
            i = next(i for i, b in enumerate(blocks) if b["legacy_id"] == "Ballast")
            cargo_y = 4.5 if category == "support" else 1.0 + 0.01 * i
            log = out.parent / "cargo_mass.jsonl"
            with open(log, "w") as handle:
                handle.write(json.dumps({"event": "start", "k": 1.0, "switch_frames": 0}) + "\n")
                # the plugin logs every physics frame until the simulation stops, which is
                # after the last telemetry sample (offset 0 here)
                last = round(max(trajgen.telemetry_times(plan.duration)) * 100)
                for frame in range(1, last + 1):
                    t = frame / 100
                    handle.write(json.dumps({"frame": frame, "t": t, "mass": ramp.mass_at(t), "rb_mass": ramp.mass_at(t),
                                             "y": cargo_y}) + "\n")
                handle.write(json.dumps({"event": "stop", "post_step_mismatch": 0}) + "\n")
            meta["cargo_mass_log"] = str(log)
        return RunOutput(out, meta)


def make_sample(root: Path, cell: str, name: str, ops, *, check_ok=True, checks: dict | None = None,
                session_status: str | None = None, final_reply: str | None = FINAL_REPLY) -> Path:
    """A sample directory as the build stage leaves it. ``check_ok=False`` fails
    the gate check machine_files_present; ``checks`` overrides single checks."""
    sample = root / "batchA" / "modelX" / cell / name
    (sample / "machine").mkdir(parents=True)
    if ops is not None:
        support.build_history(ops, sample / "machine", f"{cell}_{name}")   # writes the .bsg find_history keys on
    if final_reply is not None:
        (sample / "final_reply.txt").write_text(final_reply)
    (sample / "usage.json").write_text(json.dumps(USAGE))
    table = {k: True for k in GATE_CHECKS + FLAG_CHECKS}
    table["machine_files_present"] = check_ok
    table.update(checks or {})
    (sample / "build_check.json").write_text(json.dumps({"ok": all(table.values()), "checks": table}))
    meta = {"cell": cell}
    if session_status is not None:
        meta["status"] = session_status
    (sample / "meta.json").write_text(json.dumps(meta))
    return sample


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()
        cls.root = support.scratch("runs_")
        cls.game = FakeGame()
        cls.prompts = ControllerPrompts(controller_system_prompt(cls.paths.v1_root, "BLOCKS", "NOTE"),
                                        cls.paths.v1_root)
        S = lambda cell, name, ops, **kw: make_sample(cls.root, cell, name, ops, **kw)
        cls.samples = {"t_ok": S("transport_soft", "s01", support.CAR),
                       "t_bad": S("transport_soft", "s02", support.CAR),
                       "t_still": S("transport_soft", "s03", support.CAR),
                       "t_none": S("transport_soft", "s04", None),
                       "support": S("support_soft", "s01", support.BRIDGE_EAST),
                       "lift_soft": S("lift_soft", "s01", support.ROCKET),
                       "lift_med": S("lift_medium", "s01", support.ROCKET),
                       "lift_invalid": S("lift_medium", "s02", support.ROCKET, check_ok=False)}
        S("lift_hard", "s01", support.ROCKET)   # never evaluated
        cls.agents = {"t_ok": FakeAgent(reply_binding("spinning_forward")), "t_bad": FakeAgent("I refuse."),
                      "t_still": FakeAgent(reply_binding("spinning_backward")), "t_none": FakeAgent("")}
        cls.results = {}
        for key, sample in cls.samples.items():
            cls.results[key] = evaluate_sample(sample, runner=cls.game, paths=cls.paths, control_agent=cls.agents.get(key),
                                               prompts=cls.prompts, attempt="20260101T000000Z")

    def test_transport_three_trials(self):
        r = self.results["t_ok"]
        self.assertEqual((r["status"], r["success"]), ("scored", True))
        self.assertEqual([t["status"] for t in r["trials"]], ["scored"] * 3)
        self.assertEqual(r["metric"]["name"], "max_transport_distance")
        self.assertGreater(r["metric"]["value"], 10.0)
        self.assertEqual([len(m) for m in self.agents["t_ok"].calls], [2, 7, 12])
        self.assertEqual(len(r["control_usage"]), 3)
        out = self.samples["t_ok"] / "eval" / "20260101T000000Z"
        for n in (1, 2, 3):
            for f in ("controller.py", "schedule.json", "control.json", "control_round.json", "score.json",
                      "legacy_rows.csv", "telemetry.csv", "run_plan.json"):
                self.assertTrue((out / f"trial_{n}" / f).is_file(), f"trial_{n}/{f}")
        plan = json.loads((out / "trial_1" / "run_plan.json").read_text())
        self.assertEqual(plan["besiege_cli_args"][:2], ["run", "--bsg"])
        self.assertIn("--pre-controller-hold", plan["besiege_cli_args"])
        second = json.loads((out / "trial_2" / "control_round.json").read_text())["messages"]
        self.assertTrue(second[3]["content"].startswith("The simulation trajectory (time, Block ID, x, y, z): "))
        self.assertTrue(json.loads((out / "result.json").read_text())["success"])

    def test_transport_failures(self):
        self.assertEqual(self.results["t_bad"]["status"], "control_parse_failed")
        self.assertEqual(len(self.agents["t_bad"].calls), 3)   # 1.0 retried an unparsable reply
        self.assertFalse(self.results["t_bad"]["success"])
        self.assertIsNone(self.results["t_bad"]["metric"]["value"])
        still = self.results["t_still"]
        self.assertEqual(len(still["trials"]), 2)
        self.assertEqual(still["trials"][1]["stop_reason"], "The machine is not moving at all")
        self.assertFalse(still["success"])
        self.assertEqual(self.results["t_none"]["status"], "no_machine")
        self.assertEqual(self.results["lift_invalid"]["status"], "build_invalid")

    def test_support_and_lift(self):
        for key in ("support", "lift_soft", "lift_med"):
            self.assertEqual((self.results[key]["status"], self.results[key]["success"]), ("scored", True), key)
        self.assertEqual(self.results["support"]["metric"]["name"], "ballast_max_weight_at_threshold")
        self.assertEqual(self.results["support"]["metric"]["value"], 1500.0)
        self.assertEqual(self.results["lift_soft"]["metric"]["name"], "effective_propulsion_force")
        lift_plan = [p for p in self.game.plans if "lift_medium" in str(p.trial_dir)][0]
        self.assertEqual(lift_plan.controller.name, "timeline.json")

    def test_summary(self):
        rows = summarize.summarize(summarize.latest_results(self.root))
        by_cell = {r["cell"]: r for r in rows}
        t = by_cell["transport_soft"]
        self.assertEqual((t["samples"], t["successes"]), (4, 1))
        self.assertEqual(t["status_per_sample"], ["scored", "control_parse_failed", "scored", "no_machine"])
        calls = 3 + 3 + 2   # t_ok three rounds, t_bad one round called three times, t_still two rounds
        self.assertEqual((t["input_tokens"], t["output_tokens"], t["requests"]),
                         (4 * 100 + calls * 10, 4 * 50 + calls * 5, 4 * 3 + calls * 1))
        self.assertEqual(by_cell["lift_hard"]["status_per_sample"], ["not_evaluated"])
        self.assertEqual([r["cell"] for r in rows],
                         ["transport_soft", "support_soft", "lift_soft", "lift_medium", "lift_hard"])
        text = summarize.markdown(rows)
        self.assertTrue(text.startswith(summarize.SCOPE_NOTE))

    def test_scoring_is_deterministic(self):
        """Same telemetry, same score; row order does not matter."""
        trial = self.samples["t_ok"] / "eval" / "20260101T000000Z" / "trial_1"
        blocks = json.loads((trial.parents[0] / "prepared" / "block_map.json").read_text())["blocks"]
        ident = {"model_name": "m", "machine_id": "x", "round_suffix": "_1", "machine_bsg_path": "",
                 "machine_json_path": ""}
        first = json.dumps(score_trial(trial / "telemetry.csv", blocks, "transport", "soft", ident, EvalConfig())[0])
        again = json.dumps(score_trial(trial / "telemetry.csv", blocks, "transport", "soft", ident, EvalConfig())[0])
        self.assertEqual(first, again)
        lines = (trial / "telemetry.csv").read_text().splitlines()
        body = lines[1:]
        random.Random(3).shuffle(body)
        shuffled = trial.parent / "shuffled.csv"
        shuffled.write_text("\n".join([lines[0]] + body) + "\n")
        third = json.dumps(score_trial(shuffled, blocks, "transport", "soft", ident, EvalConfig())[0])
        self.assertEqual(first, third)

    def test_run_failure_is_recorded(self):
        class Broken:
            loads_support_levels = True

            def run(self, plan):
                raise RuntimeError("game crashed")
        sample = make_sample(self.root.parent / (self.root.name + "_broken"), "support_soft", "s01", support.BRIDGE_EAST)
        result = evaluate_sample(sample, runner=Broken(), paths=self.paths, attempt="a1")
        self.assertEqual((result["status"], result["success"]), ("run_failed", False))
        error = json.loads((sample / "eval" / "a1" / "trial_1" / "run_error.json").read_text())
        self.assertIn("game crashed", error["error"])
        self.assertEqual(error["run_attempts"], 3)
        with self.assertRaises(FileExistsError):
            evaluate_sample(sample, runner=Broken(), paths=self.paths, attempt="a1")

    def test_support_refused_without_levels(self):
        class NoLevels(FakeGame):
            loads_support_levels = False
        sample = make_sample(self.root.parent / (self.root.name + "_nolevel"), "support_soft", "s01", support.BRIDGE_EAST)
        game = NoLevels()
        result = evaluate_sample(sample, runner=game, paths=self.paths, attempt="a1",
                                 cfg=replace(EvalConfig(), support_protocol="refuse"))
        self.assertEqual((result["status"], result["success"]), (SUPPORT_REFUSED, False))
        self.assertEqual(game.plans, [])
        self.assertFalse((sample / "eval" / "a1" / "prepared").exists())

    def test_prestart_only_for_python_controllers(self):
        live = RunPlan(Path("/x"), Path("/x.bsg"), Path("/c.py"), "BARREN EXPANSE", 30.0, 25, 25)
        timeline = RunPlan(Path("/x"), Path("/x.bsg"), Path("/t.json"), "BARREN EXPANSE", 30.0, 25, 25)
        self.assertIn("--controller-prestart", live.besiege_cli_args())
        self.assertNotIn("--controller-prestart", timeline.besiege_cli_args())

    def test_game_not_connected(self):
        plan = RunPlan(Path("/x"), Path("/x.bsg"), Path("/c.py"), "BARREN EXPANSE", 30.0, 25, 25)
        with self.assertRaises(NotImplementedError):
            GameNotConnected().run(plan)


if __name__ == "__main__":
    unittest.main()
