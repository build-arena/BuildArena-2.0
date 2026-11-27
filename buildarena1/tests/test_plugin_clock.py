"""Support judging on the cargo-mass plugin's clock: rows are timed from the
plugin's first physics frame, samples from before it are dropped, and the
cargo rows follow 1.0's print frames, so the result no longer depends on how
many frames telemetry ran before the physics started."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import support
from buildarena1.evalkit import cargo_plugin, scoring
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.judge import legacy_rows
from buildarena1.evalkit.legacy import SUPPORT_RAMP
from buildarena1.evalkit.legacy_log import rows_to_csv_text
from buildarena1.evalkit.runner import cargo_log_problem, summarize_cargo_log

CARGO = "cccccccc-0000-0000-0000-000000000035"
BLOCKS = [{"legacy_id": "Ballast", "guid": CARGO, "name_v1": "Ballast", "build_id": None}]


def review_height(t: float) -> float:
    """A cargo trajectory on the plugin clock that broke an earlier judging (found in review)."""
    if t <= 1.4:
        return max(4.8, 9 - 4.905 * max(t, 0.0) ** 2)
    return max(1.0, 4.8 - 2.75 * (t - 1.4) ** 2)


def write_run(tmp: Path, offset: int, height=review_height, seconds: float = 17.2) -> dict:
    """A run as the runner leaves it: the plugin's per-frame log, and 25 Hz telemetry
    whose clock ran ``offset`` frames before the physics did (the cargo frozen
    at its first height in those frames)."""
    tmp.mkdir(parents=True, exist_ok=True)
    last = int(seconds * 100)
    lines = [{"event": "start", "switch_frames": offset + 2}]
    lines += [{"frame": f, "t": f / 100, "mass": SUPPORT_RAMP.mass_at(f / 100), "y": height(f / 100)}
              for f in range(1, last + 1)]
    lines.append({"event": "stop", "why": "simulation stopped", "frames": last, "post_step_mismatch": 0})
    log = tmp / "cargo_mass.jsonl"
    log.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    rows = ["sample_sequence,simulation_time,block_guid,x,y,z"]
    for seq, k in enumerate(range(4, last + offset + 1, 4)):
        f = k - offset
        y = height(f / 100) if f >= 1 else height(0.0)
        rows.append(f"{seq},{k / 100!r},{CARGO},0.5,{y:.8f},0.25")
    tel = tmp / "telemetry.csv"
    tel.write_text("\n".join(rows) + "\n")
    clock = cargo_plugin.align(log, tel, CARGO, tmp / "trace.jsonl")
    return {"telemetry": tel, "clock": clock}


def judge(run: dict, *, plugin_clock: bool) -> dict:
    cfg = EvalConfig()
    if plugin_clock:
        rows = legacy_rows(run["telemetry"], BLOCKS, "support", "soft", cfg, cargo_clock=run["clock"])
    else:   # the judging of commit 3eb7117: telemetry clock, mass from the aligned trace
        rows = legacy_rows(run["telemetry"], BLOCKS, "support", "soft", cfg,
                           controller_trace=Path(run["clock"]["aligned_trace"]))
    stats = scoring.ballast_stats(rows_to_csv_text(rows))
    passed = stats["hold_time_above_threshold"] >= scoring.S_HOLD_THRESHOLD and not stats["initial_height_above_threshold"]
    return {"hold": round(stats["hold_time_above_threshold"], 6), "pass": passed,
            "metric": stats["max_weight_at_threshold"] if passed else 0.0, "first_time": float(rows[0][0])}


class SupportOnPluginClock(unittest.TestCase):
    def test_review_counterexample(self):
        run = write_run(support.scratch("pclock_review_"), 10)
        self.assertEqual(run["clock"]["offset_frames"], 10)
        old, new = judge(run, plugin_clock=False), judge(run, plugin_clock=True)
        self.assertEqual((old["hold"], old["pass"]), (2.0, True))      # what the review reproduced
        self.assertEqual((new["hold"], new["pass"], new["metric"]), (1.9, False, 0.0))
        self.assertEqual(new["first_time"], 0.0)                     # frame 4 (0.04 s) prints as 0.0, as in 1.0

    def test_offset_shift(self):
        """The same physics with 6 and 14 frames of telemetry before it: the old
        judging flipped from fail to pass, the plugin clock gives one answer."""
        olds, news = {}, {}
        for off in (6, 10, 14):
            run = write_run(support.scratch(f"pclock_off{off}_"), off)
            olds[off], news[off] = judge(run, plugin_clock=False), judge(run, plugin_clock=True)
        self.assertFalse(olds[6]["pass"])
        self.assertTrue(olds[14]["pass"])
        self.assertEqual(news[6], news[10])
        self.assertEqual(news[6], news[14])
        self.assertFalse(news[6]["pass"])

    def test_rows_on_v1_print_frames(self):
        run = write_run(support.scratch("pclock_rows_"), 7)
        rows = legacy_rows(run["telemetry"], BLOCKS, "support", "soft", EvalConfig(quantize_like_lua=False),
                           cargo_clock=run["clock"])
        times = sorted({float(r[0]) for r in rows})
        self.assertEqual(times[:3], [0.04, 0.08, 0.12])              # frames 4, 8, 12 whatever the offset
        weight = {float(r[0]): float(r[2]) for r in rows if len(r) == 3}
        self.assertAlmostEqual(weight[2.0], SUPPORT_RAMP.mass_at(2.0), places=4)


class PluginLogCompleteness(unittest.TestCase):
    """A run backs a score only with a complete cargo-mass log."""

    def check(self, tail: list[dict], frames=range(1, 201), events=()) -> str | None:
        path = support.scratch("cmcheck_") / "cargo_mass.jsonl"
        lines = [{"event": "start"}] + [{"frame": f, "t": f / 100, "mass": 5.0} for f in frames] + list(events) + tail
        path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
        return cargo_log_problem(summarize_cargo_log(path), {"last_time": 2.0})

    def test_complete_log_passes(self):
        self.assertIsNone(self.check([{"event": "stop", "post_step_mismatch": 0}]))

    def test_incomplete_logs_fail(self):
        mismatch = [{"event": "post_step_mismatch", "frame": 50, "post": 5.0, "set": 12.0}]
        cases = {
            "no stop": ([], range(1, 201), ()),
            "error stop without the count": ([{"event": "stop", "error": "cargo destroyed"}], range(1, 201), mismatch),
            "count missing": ([{"event": "stop", "frames": 200}], range(1, 201), ()),
            "mismatch event, count 0": ([{"event": "stop", "post_step_mismatch": 0}], range(1, 201), mismatch),
            "gap": ([{"event": "stop", "post_step_mismatch": 0}], [*range(1, 100), *range(101, 201)], ()),
            "ends early": ([{"event": "stop", "post_step_mismatch": 0}], range(1, 120), ()),
        }
        for name, (tail, frames, events) in cases.items():
            self.assertIsNotNone(self.check(tail, frames, events), name)


class LogCoverage(unittest.TestCase):
    """The plugin log is checked against the telemetry at the offset measured for
    the run, and per-frame lines may carry ``"event": "frame"``."""

    def rewrite_log(self, log: Path, keep) -> None:
        lines = [json.loads(l) for l in log.read_text().splitlines()]
        log.write_text("\n".join(json.dumps(l) for l in (keep(l) for l in lines) if l is not None) + "\n")

    def test_a_log_short_of_the_telemetry_is_refused(self):
        tmp = support.scratch("pclock_short_")
        run = write_run(tmp, 9)
        last = run["clock"]["frames"]
        # 20 frames short: within the 40-frame bound the runner checks, not at the measured offset 9
        self.rewrite_log(tmp / "cargo_mass.jsonl", lambda l: None if l.get("frame", 0) > last - 20 else l)
        summary = summarize_cargo_log(tmp / "cargo_mass.jsonl")
        tel = {"last_time": (last + 9) / 100}
        self.assertIsNone(cargo_log_problem(summary, tel))
        with self.assertRaisesRegex(ValueError, "measured offset of 9"):
            cargo_plugin.align(tmp / "cargo_mass.jsonl", run["telemetry"], CARGO, tmp / "t2.jsonl")

    def test_per_frame_lines_may_say_event_frame(self):
        tmp = support.scratch("pclock_event_")
        run = write_run(tmp, 9)
        self.rewrite_log(tmp / "cargo_mass.jsonl", lambda l: l if "event" in l else {**l, "event": "frame"})
        clock = cargo_plugin.align(tmp / "cargo_mass.jsonl", run["telemetry"], CARGO, tmp / "t2.jsonl")
        self.assertEqual((clock["offset_frames"], clock["frames"]), (9, run["clock"]["frames"]))
        summary = summarize_cargo_log(tmp / "cargo_mass.jsonl")
        self.assertEqual(summary["frames"], run["clock"]["frames"])
        self.assertIsNone(cargo_log_problem(summary, {"last_time": (run["clock"]["frames"] + 9) / 100}))


if __name__ == "__main__":
    unittest.main()
