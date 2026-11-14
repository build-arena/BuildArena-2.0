"""Cargo mass in the 1.0-style log comes from the controller's send trace
(what the run really sent), not from the ramp formula."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from . import trajgen
from .trajgen import Scenario
from buildarena1.evalkit import legacy as L
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.judge import legacy_rows, score_trial
from buildarena1.evalkit.legacy_log import read_mass_trace

IDENT = {"model_name": "m", "machine_id": "x", "round_suffix": "_1", "machine_bsg_path": "", "machine_json_path": ""}


def write_trace(path: Path, records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


class Lookup(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="trace_"))

    def test_value_in_effect_is_the_last_one_sent_before(self):
        path = write_trace(self.dir / "t.jsonl", [{"t": 0.2, "held": [], "mass": 20.0},
                                                  {"t": 0.12, "held": [], "mass": 10.0},
                                                  {"t": 0.04, "held": []}])
        trace = read_mass_trace(path, initial=5.0)
        self.assertEqual(trace.times, (0.12, 0.2))
        got = [trace.effective_at(t) for t in (0.04, 0.12, 0.16, 0.2, 0.24, 9.0)]
        self.assertEqual(got, [5.0, 5.0, 10.0, 10.0, 20.0, 20.0])

    def test_delay(self):
        path = write_trace(self.dir / "t.jsonl", [{"t": 0.12, "mass": 10.0}])
        trace = read_mass_trace(path, initial=5.0, delay=0.05)
        self.assertEqual([trace.effective_at(t) for t in (0.16, 0.18)], [5.0, 10.0])


class InTheLog(unittest.TestCase):
    """A support run where the controller sent every 0.1 s on the 25 Hz samples
    0.04, 0.12, 0.2, 0.32, ... (what evalkit's runtime controller does)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="trace_log_"))
        sc = Scenario("support", "soft", ["Starting Block", "Small Wooden Block"],
                      lambda lid, t: (0.0, 6.0 if lid == "Ballast" else 5.5, 0.0, False), cargo=True, duration=17.0)
        cls.blocks = sc.blocks()
        cls.ticks = trajgen.write(sc, cls.dir / "telemetry.csv")
        sent, due = [], 0.0
        for t, _ in cls.ticks:
            if t + 1e-4 >= due:
                sent.append({"t": t, "held": [], "mass": L.SUPPORT_RAMP.mass_at(t)})
                due = (int(t / 0.1 + 1e-4) + 1) * 0.1
        cls.sent = sent
        write_trace(cls.dir / "run" / "controller_trace.jsonl", sent)

    def test_logged_mass_is_the_sent_value_in_effect(self):
        cfg = replace(EvalConfig(), quantize_like_lua=False)
        rows = legacy_rows(self.dir / "telemetry.csv", self.blocks, "support", "soft", cfg)
        masses = {float(r[0]): float(r[2]) for r in rows if len(r) == 3}
        sent = [(s["t"], s["mass"]) for s in self.sent]
        for t, mass in masses.items():
            before = [m for ts, m in sent if ts < t]
            self.assertEqual(mass, before[-1] if before else 5.0, t)
        formula_gap = max(L.SUPPORT_RAMP.mass_at(t) - m for t, m in masses.items() if t <= 15.5)
        self.assertGreater(formula_gap, 5.0)   # the formula would overstate the support load

    def test_trace_is_required_for_cargo_cells(self):
        bare = Path(tempfile.mkdtemp(prefix="no_trace_"))
        (bare / "telemetry.csv").write_bytes((self.dir / "telemetry.csv").read_bytes())
        with self.assertRaises(FileNotFoundError):
            score_trial(bare / "telemetry.csv", self.blocks, "support", "soft", IDENT, EvalConfig())
        explicit, _ = score_trial(bare / "telemetry.csv", self.blocks, "support", "soft", IDENT, EvalConfig(),
                                  controller_trace=self.dir / "run" / "controller_trace.jsonl")
        found, _ = score_trial(self.dir / "telemetry.csv", self.blocks, "support", "soft", IDENT, EvalConfig())
        self.assertEqual(explicit["ballast_max_weight"], found["ballast_max_weight"])
        self.assertEqual(found["ballast_max_weight"], 1500.0)


if __name__ == "__main__":
    unittest.main()
