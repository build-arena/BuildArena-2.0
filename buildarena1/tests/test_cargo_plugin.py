"""The cargo mass follows 1.0's curve through the cargo-mass plugin; 1.0's start value and placement; the judge reads the
plugin's per-frame log; support on the banks is judged the 1.0 way."""

from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from . import support
from . import trajgen
from .test_sample import FakeAgent, FakeGame, make_sample, reply_binding
from buildarena1.evalkit import cargo_plugin, summarize
from buildarena1.evalkit.legacy import SUPPORT_RAMP, TRANSPORT_RAMP
from buildarena1.evalkit.legacy_log import read_mass_trace
from buildarena1.evalkit.prompts import ControllerPrompts, controller_system_prompt
from buildarena1.evalkit.sample import evaluate_sample
from buildarena1.evalkit.runner import summarize_cargo_log


class NoLevels(FakeGame):
    loads_support_levels = False


class Sinking(NoLevels):
    """The cargo drops below 4 at ``at`` seconds."""
    at = 3.0

    def run(self, plan):
        out = super().run(plan)
        lines = Path(out.telemetry_csv).read_text().splitlines()
        header = lines[0].split(",")
        manifest = json.loads((plan.trial_dir.parent / "prepared" / "block_map.json").read_text())
        cargo = next(b["guid"] for b in manifest["blocks"] if b["legacy_id"] == "Ballast")
        rows = []
        for line in lines[1:]:
            v = line.split(",")
            if v[header.index("block_guid")] == cargo and float(v[header.index("simulation_time")]) >= self.at:
                v[header.index("y")] = "1.0"
            rows.append(",".join(v))
        Path(out.telemetry_csv).write_text("\n".join([lines[0]] + rows) + "\n")
        log = Path(out.meta["cargo_mass_log"])      # the plugin reads the same height (offset 0)
        recs = [json.loads(l) for l in log.read_text().splitlines()]
        for r in recs:
            if "frame" in r and "event" not in r and r["frame"] / 100 >= self.at - 1e-9:
                r["y"] = 1.0
        log.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        return out


def cargo_block(manifest: dict) -> ET.Element:
    guid = next(b["guid"] for b in manifest["blocks"] if b["legacy_id"] == "Ballast")
    return next(b for b in ET.parse(manifest["bsg"]).getroot().find("Blocks") if b.get("guid") == guid)


class PluginCurve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = support.v2_ready()
        cls.prompts = ControllerPrompts(controller_system_prompt(cls.paths.v1_root, "B", "N"), cls.paths.v1_root)

    def test_transport_follows_the_1_0_curve(self):
        sample = make_sample(support.scratch("curve_"), "transport_medium", "s01", support.CAR)
        game = FakeGame()
        result = evaluate_sample(sample, runner=game, paths=self.paths, attempt="a1",
                                 control_agent=FakeAgent(reply_binding("spinning_forward")), prompts=self.prompts)
        self.assertEqual(result["status"], "scored")
        plan = game.plans[0]
        self.assertEqual({k: plan.cargo_curve[k] for k in ("mass_min", "mass_max", "ramp_duration", "ramp_start",
                                                            "clamp_low", "initial_slider")},
                         {"mass_min": 5.0, "mass_max": 50.0, "ramp_duration": 2.0, "ramp_start": 0.5,
                          "clamp_low": 0.005, "initial_slider": 5.0})
        out = sample / "eval" / "a1"
        self.assertIsNone(json.loads((out / "trial_1" / "schedule.json").read_text())["ballast"])   # nothing sent
        manifest = json.loads((out / "prepared" / "block_map.json").read_text())
        block = cargo_block(manifest)
        self.assertEqual(float(next(n for n in block.find("Data") if n.get("key") == "bmt-mass").text), 5.0)
        self.assertAlmostEqual(float(block.find("Transform/Position").get("y")),
                               manifest["placement"]["stats"]["top_y"] + 3.0)   # 1.0's placement
        self.assertNotIn("cargo_mass_in_bsg", result)
        # the judge's mass for a telemetry sample at T is the one set for plugin frame round(T * 100)
        # (offset 0 here: the fake cargo does not move, so the alignment is flagged ambiguous)
        clock = result["trials"][0]["cargo_mass_clock"]
        self.assertEqual((clock["offset_frames"], clock["ambiguous"]), (0, True))
        trace = read_mass_trace(Path(clock["aligned_trace"]), initial=5.0)
        for t in trajgen.telemetry_times(10.0)[:80]:
            self.assertAlmostEqual(trace.effective_at(t), TRANSPORT_RAMP.mass_at(round(t * 100) / 100), places=4)
        weights = [r.split(",") for r in (out / "trial_1" / "legacy_rows.csv").read_text().splitlines()]
        weights = [float(r[2]) for r in weights if len(r) == 3 and r[1] == "ID_Ballast"]
        self.assertAlmostEqual(weights[0], 5.2, places=6)       # 1.0's floor: 5 + 45 * 0.005 at 0.04 s
        self.assertAlmostEqual(weights[-1], 50.0)

    def test_support_on_banks_judged_the_1_0_way(self):
        sample = make_sample(support.scratch("curve_"), "support_soft", "s01", support.BRIDGE_EAST)
        game = NoLevels()
        result = evaluate_sample(sample, runner=game, paths=self.paths, attempt="a1")
        self.assertEqual((result["status"], result["success"]), ("scored", True))
        manifest = json.loads((sample / "eval" / "a1" / "prepared" / "block_map.json").read_text())
        self.assertEqual(game.plans[0].cargo_curve["bank_guids"], [b["guid"] for b in manifest["terrain"]["banks"]])
        self.assertEqual((game.plans[0].cargo_curve["mass_max"], game.plans[0].duration), (1500.0, 17.0))
        self.assertIn("support_terrain:ballast_banks", result["flags"])
        self.assertNotIn("support_protocol", result)
        self.assertEqual(result["metric"], {"name": "ballast_max_weight_at_threshold", "value": 1500.0})

    def test_support_threshold_mass_and_two_means(self):
        class AtStart(Sinking):
            at = 0.0
        root = support.scratch("curve_sum_")
        ok, late, bad = (make_sample(root, "support_soft", f"s0{i}", support.BRIDGE_EAST) for i in (1, 2, 3))
        evaluate_sample(ok, runner=NoLevels(), paths=self.paths, attempt="a1")
        sank = evaluate_sample(late, runner=Sinking(), paths=self.paths, attempt="a1")
        failed = evaluate_sample(bad, runner=AtStart(), paths=self.paths, attempt="a1")
        score = json.loads((late / "eval" / "a1" / "trial_1" / "score.json").read_text())
        self.assertGreater(score["hold_time_above_threshold"], 2.0)        # 1.0: 2 s above 4 passes
        self.assertTrue(sank["success"])
        # the mass set for the frame of the first sample below 4 (1.0 printed that row with it)
        first_below = next(t for t in trajgen.telemetry_times(17.0) if t >= Sinking.at)
        expect = float("%.1f" % SUPPORT_RAMP.mass_at(round(first_below * 100) / 100))
        self.assertAlmostEqual(score["ballast_max_weight_at_threshold"], expect, places=6)
        self.assertFalse(failed["success"])
        rows = summarize.summarize(summarize.latest_results(root))
        row = rows[0]
        m = score["ballast_max_weight_at_threshold"]
        self.assertAlmostEqual(row["success_metric_mean"], (1500.0 + m) / 2)
        self.assertAlmostEqual(row["metric_mean_fail_as_0"], (1500.0 + m + 0.0) / 3)
        self.assertIn("failures as 0", summarize.markdown(rows))


class ClockAlignment(unittest.TestCase):
    """Telemetry's clock runs a few frames ahead of the plugin's; the offset is read off the falling cargo."""

    def write(self, tmp: Path, offset: int, plugin_noise: float = 0.0) -> tuple[Path, Path]:
        y = lambda frame: 7.0 - 0.5 * 9.81 * (max(frame, 0) / 100) ** 2 if frame < 90 else 7.0 - 0.5 * 9.81 * 0.81
        log, tel = tmp / "cargo_mass.jsonl", tmp / "telemetry.csv"
        lines = [{"event": "start", "switch_frames": offset + 2}]
        lines += [{"frame": f, "t": f / 100, "mass": SUPPORT_RAMP.mass_at(f / 100), "y": y(f) + plugin_noise}
                  for f in range(1, 400)]
        log.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
        rows = ["sample_sequence,simulation_time,block_guid,x,y,z"]
        for seq, k in enumerate(range(4, 400, 4)):
            rows.append(f"{seq},{k / 100!r},CARGO,0,{y(k - offset):.8f},0")
        tel.write_text("\n".join(rows) + "\n")
        return log, tel

    def test_offset_found_and_masses_paired(self):
        tmp = support.scratch("align_")
        log, tel = self.write(tmp, 9)
        got = cargo_plugin.align(log, tel, "cargo", tmp / "trace.jsonl")
        self.assertEqual((got["offset_frames"], got["ambiguous"], got["switch_frames"]), (9, False, 11))
        trace = read_mass_trace(tmp / "trace.jsonl", initial=5.0)
        self.assertAlmostEqual(trace.effective_at(2.00), SUPPORT_RAMP.mass_at(1.91), places=4)   # plugin frame 191

    def test_no_match_is_an_error(self):
        tmp = support.scratch("align_bad_")
        log, tel = self.write(tmp, 9, plugin_noise=0.05)
        with self.assertRaises(ValueError):
            cargo_plugin.align(log, tel, "CARGO", tmp / "trace.jsonl")


class LogSummary(unittest.TestCase):
    def test_summarize_cargo_log(self):
        path = support.scratch("cmlog_") / "cargo_mass.jsonl"
        lines = [{"event": "start", "k": 1.0}, {"event": "banks_start", "frame": 0, "banks": [{"rb_mass": 1e5}]},
                 {"t": 0.01, "mass": 12.475}, {"event": "override", "frame": 1, "read": 3.0, "set": 12.475},
                 {"t": 0.02, "mass": 12.475}, {"event": "stop", "frames": 2, "overrides": 1}]
        path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
        got = summarize_cargo_log(path)
        self.assertEqual((got["frames"], got["overrides"], got["first"]["t"], got["last"]["t"]), (2, 1, 0.01, 0.02))
        self.assertEqual(got["banks_start"], [{"rb_mass": 1e5}])
        self.assertEqual(summarize_cargo_log(path.with_name("missing.jsonl")), {"error": "no log"})


if __name__ == "__main__":
    unittest.main()
