"""Ported judges vs the 1.0 originals on the same trajectories.

Each case writes one synthetic trajectory twice: as a 2.0 telemetry.csv for
evalkit, and as 1.0 Lua log text run through 1.0's own save_to_csv /
clean_simulation_csv and analyze_simulation_<category>. Every 1.0 result field
except paths and names/identity must be equal (2.0-only v2 is excluded;
floats use exact equality, with two NaNs equal), with
lift_success_rule="ba1" selecting the original lift criterion.

With BA1_ROOT set (a BuildArena 1.0 clone at c3005b9, plus matplotlib, see
requirements-v1.txt) the 1.0 side runs live, and ``FixtureAgreesWithLiveV1``
also checks the stored results below against it. Without BA1_ROOT the 1.0 side
is read from fixtures/v1_judge_results.json, the 1.0 judges' results on these
same trajectories recorded with BA1_ROOT set (Linux x86_64, Python 3.12,
pandas 2.3.3, numpy 2.3.2). Each stored result carries a digest of the input
the 1.0 judge got (category, level, Lua log text, block list); a run whose
input no longer has that digest fails instead of using the stale result. To
record them again:
    BA1_ROOT=<1.0 clone> BA1_RECORD_FIXTURE=buildarena1/tests/fixtures/v1_judge_results.json \
        python -m unittest buildarena1.tests.test_scoring_equivalence
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from . import fuzzgen
from . import v1_harness
from . import trajgen
from .trajgen import Scenario

from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.judge import score_trial

SKIP = {"machine_bsg_path", "machine_json_path", "model_name", "machine_id", "round_suffix", "v2"}
IDENT = {"model_name": "model", "machine_id": "m0001", "round_suffix": "_1",
         "machine_bsg_path": "", "machine_json_path": ""}
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "v1_judge_results.json"
# resolved now: importing the 1.0 judges changes the working directory (v1_harness)
RECORD = str(Path(os.environ["BA1_RECORD_FIXTURE"]).resolve()) if os.environ.get("BA1_RECORD_FIXTURE", "").strip() else None
LIVE: dict[str, dict] = {}       # run key -> the 1.0 result computed live in this process
LIVE_INPUTS: dict[str, str] = {}  # run key -> digest of the input the 1.0 judge got


def run_key(case, quantize: bool, order: str) -> str:
    return f"{case}|{'q' if quantize else 'raw'}|{order}"


def _plain(value):
    """A 1.0 result as JSON data (numpy scalars as Python ones)."""
    return json.loads(json.dumps(value, default=lambda o: o.item()))


def stored_v1() -> dict[str, dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["results"]


def stored_inputs() -> dict[str, str]:
    return json.loads(FIXTURE.read_text(encoding="utf-8")).get("inputs", {})


def input_digest(category: str, level: str, lua_text: str, block_ids: list) -> str:
    """sha256 of what the 1.0 judge reads for one run."""
    payload = json.dumps([category, level, lua_text, block_ids], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_lua_equivalent_trace(path: Path, ticks, mass_max: float, ramp: float) -> None:
    """A controller trace whose value in effect at each telemetry tick is the
    mass 1.0's Lua set and printed at that time (build.py:1289-1292): each tick's
    value is sent just before the tick."""
    with open(path, "w", encoding="utf-8") as handle:
        for t, _ in ticks:
            mass = 5.0 + (mass_max - 5.0) * max(min((t - 0.5) / ramp, 1.0), 0.005)
            handle.write(json.dumps({"t": t - 1e-3, "mass": mass}) + "\n")


def lin(a: float, b: float, t0: float, t1: float, t: float) -> float:
    """a before t0, b after t1, linear in between."""
    if t <= t0:
        return a
    if t >= t1:
        return b
    return a + (b - a) * (t - t0) / (t1 - t0)


def same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


# ------------------------------------------------------------------ scenarios
def car(goal_x: float, goal_z: float, *, wheels: int = 4, start_y: float = 0.6, lift_until: float = 0.0,
        split: float = 0.0, lose_wheel_after: float | None = None, extra: list[str] | None = None):
    """Start block drives from the origin to (goal_x, goal_z) between t=2 and t=25."""
    names = ["Starting Block"] + (extra or []) + ["Powered Wheel"] * wheels
    offsets = [(1, -0.1, 1), (-1, -0.1, 1), (1, -0.1, -1), (-1, -0.1, -1), (2, -0.1, 0), (-2, -0.1, 0)]
    wheel_ids = [str(i + 1) for i, n in enumerate(names) if n == "Powered Wheel"]

    def state(lid, t):
        x, z = lin(0, goal_x, 2, 25, t), lin(0, goal_z, 2, 25, t)
        y = start_y if t <= lift_until + 0.09 else 0.6
        if lid in wheel_ids:
            k = wheel_ids.index(lid)
            if lose_wheel_after is not None and k == 2 and t > lose_wheel_after:
                return None
            ox, oy, oz = offsets[k]
            drift = lin(0, split, 10, 20, t) if k < 2 else 0.0
            return (x + ox + drift, y + oy, z + oz, False)
        return (x, y, z, False)
    return names, state


def carrier(goal_x, goal_z, *, cargo_follows: bool, wheels: int = 4, split: float = 0.0):
    names = ["Starting Block"] + ["Powered Wheel"] * wheels if wheels else ["Starting Block", "Small Wooden Block",
                                                                            "Small Wooden Block"]
    base_names, base_state = car(goal_x, goal_z, wheels=wheels, split=split)

    def state(lid, t):
        if lid == "Ballast":
            y = lin(5.0, 1.6, 0.0, 1.0, t)
            if cargo_follows:
                return (lin(0, goal_x, 2, 25, t), y, lin(0, goal_z, 2, 25, t), False)
            return (0.3, y, -0.2, False)
        return base_state(lid, t)
    return names, state


def cargo_only(height):
    """Support: the cargo's height as a function of time; other blocks static."""
    def state(lid, t):
        if lid == "Ballast":
            return (0.0, height(t), 0.0, False)
        return (float(lid), 5.5, 0.0, False)
    return state


def rocket(heights, heated_ids=(), heated_when=lambda t: True):
    def state(lid, t):
        return (0.0, heights(t) + 0.1 * (int(lid) - 2), 0.0, lid in heated_ids and heated_when(t))
    return state


WC, TORCH, WOOD, WHEEL = "Water Cannon", "Torch", "Small Wooden Block", "Powered Wheel"

CASES: dict[str, Scenario] = {}


def add(name, category, level, names, state, *, cargo=False, duration=30.0):
    CASES[name] = Scenario(category, level, names, state, cargo=cargo, duration=duration)


add("transport_soft_pass", "transport", "soft", *car(8, 8))
add("transport_soft_three_wheels", "transport", "soft", *car(8, 8, wheels=3))
add("transport_soft_distance_9_9", "transport", "soft", *car(7, 7))
add("transport_soft_distance_exact_10", "transport", "soft", *car(10.0, 0.0))
add("transport_soft_distance_10_04", "transport", "soft", *car(10.04, 0.0))
add("transport_soft_initial_height_20_0", "transport", "soft", *car(8, 8, start_y=20.0, lift_until=0.1))
add("transport_soft_initial_height_20_1", "transport", "soft", *car(8, 8, start_y=20.1, lift_until=0.1))
add("transport_soft_initial_height_19_9", "transport", "soft", *car(8, 8, start_y=19.9, lift_until=0.1))
add("transport_soft_west_south", "transport", "soft", *car(-9, -9))
add("transport_soft_wheel_lost", "transport", "soft", *car(8, 8, lose_wheel_after=10.0))
add("transport_hard_wheels_split", "transport", "hard", *carrier(8, 8, cargo_follows=True, split=5.0), cargo=True)
add("transport_medium_cargo_carried", "transport", "medium", *carrier(8, 8, cargo_follows=True), cargo=True)
add("transport_medium_cargo_dropped", "transport", "medium", *carrier(8, 8, cargo_follows=False), cargo=True)
add("transport_medium_no_wheels_hacked", "transport", "medium", *carrier(9, 9, cargo_follows=True, wheels=0),
    cargo=True)
add("transport_soft_mass_order", "transport", "soft",
    *car(8, 8, extra=[WOOD] * 5 + [TORCH, "Brace", "Winch"]))

S = dict(cargo=True, duration=17.0)
add("support_holds_high", "support", "soft", ["Starting Block", WOOD], cargo_only(lambda t: lin(9, 6, 0, 1, t)), **S)
add("support_breaks_at_9s", "support", "medium", ["Starting Block", WOOD],
    cargo_only(lambda t: lin(5.5, 2.0, 9.0, 9.5, t)), **S)
add("support_hold_just_below_2s", "support", "hard", ["Starting Block", WOOD],
    cargo_only(lambda t: 6.0 if t <= 1.92 else 1.0), **S)
add("support_hold_just_above_2s", "support", "hard", ["Starting Block", WOOD],
    cargo_only(lambda t: 6.0 if t <= 2.04 else 1.0), **S)
add("support_height_exact_4", "support", "soft", ["Starting Block", WOOD], cargo_only(lambda t: 4.0), **S)
add("support_height_3_96", "support", "soft", ["Starting Block", WOOD], cargo_only(lambda t: 3.96), **S)
add("support_initial_20_1", "support", "soft", ["Starting Block", WOOD],
    cargo_only(lambda t: 20.1 if t < 0.05 else 6.0), **S)
add("support_initial_20_0", "support", "soft", ["Starting Block", WOOD],
    cargo_only(lambda t: 20.0 if t < 0.05 else 6.0), **S)
add("support_never_above", "support", "soft", ["Starting Block", WOOD], cargo_only(lambda t: 1.0), **S)

LS = dict(duration=10.0)
add("lift_soft_two_heated", "lift", "soft", ["Starting Block", WOOD, WC, WC, TORCH],
    rocket(lambda t: 1.0, heated_ids=("3", "4")), **LS)
add("lift_soft_none_heated", "lift", "soft", ["Starting Block", WOOD, WC, WC, TORCH],
    rocket(lambda t: 1.0), **LS)
add("lift_soft_heated_one_tick", "lift", "soft", ["Starting Block", WC, TORCH],
    rocket(lambda t: 1.0, heated_ids=("2",), heated_when=lambda t: 5.0 < t < 5.05), **LS)
add("lift_soft_ratio_just_above_1", "lift", "soft", ["Starting Block", WC] + [WHEEL] * 12,
    rocket(lambda t: 1.0, heated_ids=("2",)), **LS)
add("lift_soft_ratio_just_below_1", "lift", "soft", ["Starting Block", WC, WOOD] + [WHEEL] * 12,
    rocket(lambda t: 1.0, heated_ids=("2",)), **LS)
ROCKET = ["Starting Block", WC, WC, TORCH]
add("lift_medium_pass", "lift", "medium", ROCKET, rocket(lambda t: lin(1, 35, 2, 12, t)))
add("lift_medium_height_exact_30", "lift", "medium", ROCKET, rocket(lambda t: lin(1, 29.8, 2, 12, t)))
add("lift_medium_height_30_1", "lift", "medium", ROCKET, rocket(lambda t: lin(1, 29.9, 2, 12, t)))
add("lift_medium_elevation_exact_5", "lift", "medium", ROCKET,
    rocket(lambda t: 25.7 if t <= 7.06 else 30.7))
add("lift_medium_elevation_5_1", "lift", "medium", ROCKET, rocket(lambda t: 25.7 if t <= 7.06 else 30.8))
add("lift_medium_window_edge_7_04", "lift", "medium", ROCKET,
    rocket(lambda t: 40.0 if 7.02 < t < 7.06 else (10.0 if t <= 7.0 else 12.0)))
add("lift_medium_flat_above_30", "lift", "medium", ROCKET, rocket(lambda t: 31.0))
add("lift_hard_pass_but_hacked", "lift", "hard", ["Starting Block", WC] + [WHEEL] * 3,
    rocket(lambda t: lin(1, 40, 2, 12, t)))
add("lift_hard_six_blocks_all_tracked", "lift", "hard", ["Starting Block", WC, WC, TORCH, WOOD, WOOD, "Brace"],
    rocket(lambda t: lin(1, 40, 2, 12, t), heated_ids=("2",)))

# (case, quantize, line order). Every case runs quantized; a subset also at full
# precision and with reversed log order.
RAW = {"transport_soft_distance_10_04", "transport_soft_pass", "transport_medium_cargo_carried",
       "support_height_3_96", "support_breaks_at_9s", "lift_medium_window_edge_7_04", "lift_soft_two_heated",
       "lift_medium_pass"}
REVERSED = {"support_breaks_at_9s", "support_hold_just_above_2s", "transport_hard_wheels_split",
            "lift_medium_elevation_5_1"}
RUNS = ([(c, True, "print") for c in CASES] + [(c, False, "print") for c in sorted(RAW)]
        + [(c, True, "reversed") for c in sorted(REVERSED)])

OBSERVED: dict[str, dict] = {}


class ScoringEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BA1_ROOT set means live: enter() raises if it is not a 1.0 checkout, so a
        # mistyped path cannot fall back to the stored results unnoticed
        cls.live = v1_harness.requested()
        if cls.live:
            v1_harness.enter()
        elif RECORD:
            raise RuntimeError("BA1_RECORD_FIXTURE needs BA1_ROOT: the 1.0 judges must run to record their results.")
        cls.stored = None if cls.live else stored_v1()
        cls.stored_inputs = None if cls.live else stored_inputs()
        cls.tmp = Path(tempfile.mkdtemp(prefix="equiv_"))

    def v1_side(self, key: str, run, digest: str):
        """The 1.0 result for ``key``: run live (``run()``) when 1.0 is available, else the
        stored one, which must have been computed on an input with this ``digest``."""
        if not self.live:
            if key not in self.stored:
                self.fail(f"{key}: no stored 1.0 result in {FIXTURE.name}; record it with BA1_ROOT set")
            if self.stored_inputs.get(key) != digest:
                self.fail(f"{key}: the 1.0 judge's input has changed since {FIXTURE.name} was recorded "
                          f"(stored digest {self.stored_inputs.get(key)}, now {digest}); record it again with BA1_ROOT set")
            return self.stored[key]
        LIVE_INPUTS[key] = digest
        try:
            theirs = run()
        except Exception as exc:
            theirs = {"raised": type(exc).__name__}
        LIVE[key] = _plain({k: v for k, v in theirs.items() if k not in SKIP})
        return theirs

    def compare(self, case, quantize: bool, order: str, *, ours_cfg: EvalConfig | None = None, key: str | None = None):
        """Score one case both ways; return (differing keys, ours, theirs).

        ``case`` is a CASES key or a Scenario (then ``key`` names it for the
        stored results). Either side raising is returned as {"raised": <exception
        type>} so that callers can require both to raise.
        """
        key = key or run_key(case, quantize, order)
        sc = CASES[case] if isinstance(case, str) else case
        cfg = ours_cfg or replace(EvalConfig(lift_success_rule="ba1"),
                                   quantize_like_lua=quantize, log_line_order=order)
        work = Path(tempfile.mkdtemp(prefix="case_", dir=self.tmp))
        ticks = trajgen.write(sc, work / "telemetry.csv")
        blocks = sc.blocks()
        mass_max = {"support": 1500.0, "transport": 50.0}.get(sc.category) if sc.cargo else None
        ramp = {"support": 15.0, "transport": 2.0}.get(sc.category) if sc.cargo else None
        if mass_max is not None:
            write_lua_equivalent_trace(work / "controller_trace.jsonl", ticks, mass_max, ramp)
        try:
            ours, _ = score_trial(work / "telemetry.csv", blocks, sc.category, sc.level, IDENT, cfg)
        except Exception as exc:
            ours = {"raised": type(exc).__name__}
        kept = [(t, s) for t, s in ticks if t <= sc.duration + 1e-6]
        text = v1_harness.lua_log(kept, trajgen.v1_tracked(sc, blocks), quantize=quantize,
                                  mass_max=mass_max, ramp=ramp)
        if order == "reversed":
            text = "\n".join(reversed(text.split("\n")))
        block_ids = trajgen.v1_block_id_list(blocks)
        theirs = self.v1_side(key, lambda: v1_harness.run_v1(sc.category, sc.level, text, block_ids, work),
                              input_digest(sc.category, sc.level, text, block_ids))
        if "raised" in ours or "raised" in theirs:
            both = "raised" in ours and "raised" in theirs
            return ([] if both else ["raised"]), ours, theirs
        keys = (set(ours) | set(theirs)) - SKIP
        return sorted(k for k in keys if k not in ours or k not in theirs or not same(ours[k], theirs[k])), ours, theirs

    def check(self, case: str, quantize: bool, order: str):
        differing, ours, theirs = self.compare(case, quantize, order)
        self.assertEqual(differing, [], f"{case}: " + "; ".join(
            f"{k}: ours={ours.get(k)!r} v1={theirs.get(k)!r}" for k in differing))
        if isinstance(case, str):
            OBSERVED[f"{case}|{int(quantize)}|{order}"] = {"pass": theirs["pass"], "hacked": theirs.get("hacked")}


def _make(case, quantize, order):
    return lambda self: self.check(case, quantize, order)


for _case, _q, _order in RUNS:
    setattr(ScoringEquivalence, f"test_{_case}__{'q' if _q else 'raw'}_{_order}", _make(_case, _q, _order))


EDGE = {
    # edge cases where an earlier version of the port and 1.0 disagreed
    "support_no_cargo_rows": (Scenario("support", "soft", ["Starting Block", "Small Wooden Block"],
                              lambda lid, t: None if lid == "Ballast" else (0, 5.5, 0, False),
                              cargo=True, duration=17.0), "result"),
    "transport_header_only": (Scenario("transport", "soft", ["Starting Block", "Powered Wheel"],
                              lambda lid, t: None, duration=30.0), "both_raise"),
    "lift_soft_start_block_only": (Scenario("lift", "soft", ["Starting Block"], lambda lid, t: (0, 1, 0, False),
                                   duration=10.0), "result"),
    "lift_medium_cannon_rows_missing": (Scenario("lift", "medium", ["Starting Block", "Water Cannon", "Torch"],
                                        lambda lid, t: None if lid == "2" else (0, 1 + t, 0, False),
                                        duration=30.0), "result"),
    # 1.0's cannon read raises on an empty CSV (sim_lift.py:187-190); so must the port.
    "lift_soft_cannon_no_rows": (Scenario("lift", "soft", ["Starting Block", "Water Cannon"],
                                 lambda lid, t: None, duration=10.0), "both_raise"),
}


class EdgeAndFuzzCases(ScoringEquivalence):
    """Cases where an earlier version of the port raised and 1.0 did not, and
    the cases around them where both must raise."""

    def run_edge(self, name: str, quantize: bool, order: str):
        scenario, expect = EDGE[name]
        differing, ours, theirs = self.compare(scenario, quantize, order, key=run_key(f"edge:{name}", quantize, order))
        self.assertEqual(differing, [], f"{name}: ours={ours!r} v1={theirs!r}")
        self.assertEqual("raised" in theirs, expect == "both_raise", f"{name}: v1={theirs!r}")

    def run_seed(self, seed: int, quantize: bool, order: str):
        scenario = fuzzgen.make(random.Random(seed))
        self.assertEqual((scenario.category, scenario.v1_names), ("lift", ["Starting Block"]))
        differing, ours, theirs = self.compare(scenario, quantize, order, key=run_key(f"seed:{seed}", quantize, order))
        self.assertEqual(differing, [], f"seed {seed}: ours={ours!r} v1={theirs!r}")
        self.assertNotIn("raised", theirs)


MODES = ((True, "print"), (False, "print"), (True, "reversed"))
EDGE_RUNS = [(n, q, o) for n in EDGE for q, o in MODES]
SEED_RUNS = [(s, q, o) for s in fuzzgen.START_ONLY_LIFT_SEEDS for q, o in MODES]


def _edge(name, q, o):
    return lambda self: self.run_edge(name, q, o)


def _seed(seed, q, o):
    return lambda self: self.run_seed(seed, q, o)


for _n, _q, _o in EDGE_RUNS:
    setattr(EdgeAndFuzzCases, f"test_edge_{_n}__{'q' if _q else 'raw'}_{_o}", _edge(_n, _q, _o))
for _s, _q, _o in SEED_RUNS:
    setattr(EdgeAndFuzzCases, f"test_fuzz_seed_{_s}__{'q' if _q else 'raw'}_{_o}", _seed(_s, _q, _o))


class NegativeControl(ScoringEquivalence):
    """The comparison must notice when the port is fed a different pipeline."""

    def test_changed_input_is_detected(self):
        """Stored mode only: a stored 1.0 result is not used for another input."""
        if self.live:
            self.skipTest("the 1.0 side runs live")
        real = v1_harness.lua_log
        v1_harness.lua_log = lambda *a, **k: ""
        try:
            with self.assertRaisesRegex(AssertionError, "input has changed"):
                self.compare("lift_soft_none_heated", True, "print")
        finally:
            v1_harness.lua_log = real

    def test_precision_mismatch_is_detected(self):
        wrong = replace(EvalConfig(lift_success_rule="ba1"), quantize_like_lua=False)
        differing, _, _ = self.compare("transport_soft_distance_10_04", True, "print", ours_cfg=wrong)
        self.assertIn("pass", differing)
        self.assertIn("max_transport_distance", differing)

    def test_line_order_mismatch_is_detected(self):
        wrong = replace(EvalConfig(lift_success_rule="ba1"), log_line_order="reversed")
        differing, _, _ = self.compare("support_breaks_at_9s", True, "print", ours_cfg=wrong)
        self.assertIn("ballast_max_weight_at_threshold", differing)

    def test_tracking_mismatch_is_detected(self):
        wrong = replace(EvalConfig(lift_success_rule="ba1"), heated_field="steam")
        differing, _, _ = self.compare("lift_soft_two_heated", True, "print", ours_cfg=wrong)
        self.assertIn("water_cannon_active", differing)


class ScenarioCoverage(unittest.TestCase):
    """The boundary cases must land on both sides of each criterion."""

    def test_outcomes_cover_both_sides(self):
        if len(OBSERVED) < len(RUNS):
            self.skipTest("run together with ScoringEquivalence")
        get = lambda c, q=1, o="print": OBSERVED[f"{c}|{q}|{o}"]["pass"]
        self.assertTrue(get("transport_soft_pass"))
        self.assertFalse(get("transport_soft_three_wheels"))
        self.assertFalse(get("transport_soft_distance_exact_10"))
        self.assertNotEqual(get("transport_soft_distance_10_04", 1), get("transport_soft_distance_10_04", 0))
        self.assertFalse(get("transport_soft_initial_height_20_0"))
        self.assertTrue(get("transport_soft_initial_height_19_9"))
        self.assertTrue(OBSERVED["transport_soft_initial_height_20_1|1|print"]["hacked"])
        self.assertTrue(OBSERVED["transport_medium_no_wheels_hacked|1|print"]["hacked"])
        self.assertFalse(get("transport_medium_cargo_dropped"))
        self.assertFalse(get("support_hold_just_below_2s"))
        self.assertTrue(get("support_hold_just_above_2s"))
        self.assertNotEqual(get("support_height_3_96", 1), get("support_height_3_96", 0))
        self.assertFalse(get("support_initial_20_1"))
        self.assertTrue(get("support_initial_20_0"))
        self.assertTrue(get("lift_soft_ratio_just_above_1"))
        self.assertFalse(get("lift_soft_ratio_just_below_1"))
        self.assertTrue(get("lift_soft_heated_one_tick"))
        self.assertFalse(get("lift_medium_height_exact_30"))
        self.assertTrue(get("lift_medium_height_30_1"))
        self.assertFalse(get("lift_medium_elevation_exact_5"))
        self.assertTrue(get("lift_medium_elevation_5_1"))
        self.assertNotEqual(get("lift_medium_window_edge_7_04", 1), get("lift_medium_window_edge_7_04", 0))
        self.assertTrue(OBSERVED["lift_hard_pass_but_hacked|1|print"]["hacked"])


class FixtureAgreesWithLiveV1(unittest.TestCase):
    """With BA1_ROOT set: the stored 1.0 results equal the ones just computed live,
    so the offline comparison is against what 1.0 really returns. With
    BA1_RECORD_FIXTURE also set, the live results are written there instead."""

    def test_stored_results_match_live(self):
        if not LIVE:
            self.skipTest("needs BA1_ROOT (the 1.0 side ran from the stored results)")
        if RECORD:
            self.assertEqual(sorted(LIVE), expected_keys(), "record from a run of the whole module")
            payload = {"note": "BuildArena 1.0 analyze_simulation_<category> results (c3005b9) on the trajectories "
                               "of test_scoring_equivalence; keys are run_key(); path and name fields left out.",
                       "results": dict(sorted(LIVE.items())), "inputs": dict(sorted(LIVE_INPUTS.items()))}
            Path(RECORD).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            written = json.loads(Path(RECORD).read_text(encoding="utf-8"))["results"]
            self.assertTrue(all(same(LIVE[k], v) for k, v in written.items()))
            return
        stored = stored_v1()
        missing = sorted(set(LIVE) - set(stored))
        self.assertEqual(missing, [], "runs without a stored 1.0 result")
        differing = [k for k in LIVE if not same(LIVE[k], stored[k])]
        self.assertEqual(differing, [], "stored 1.0 results differ from live 1.0 for these runs")
        inputs = stored_inputs()
        changed = sorted(k for k in LIVE_INPUTS if inputs.get(k) != LIVE_INPUTS[k])
        self.assertEqual(changed, [], "the 1.0 judge's input differs from the one the stored result was computed on")

    def test_fixture_covers_every_run(self):
        if RECORD:
            self.skipTest("recording")
        self.assertEqual(sorted(stored_v1()), expected_keys())
        self.assertEqual(sorted(stored_inputs()), expected_keys())


def expected_keys() -> list[str]:
    return sorted([run_key(c, q, o) for c, q, o in RUNS]
                  + [run_key(f"edge:{n}", q, o) for n, q, o in EDGE_RUNS]
                  + [run_key(f"seed:{s}", q, o) for s, q, o in SEED_RUNS])


def load_tests(loader, tests, pattern):
    """Equivalence first so the coverage check sees every observation."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(ScoringEquivalence))
    suite.addTests(loader.loadTestsFromTestCase(ScenarioCoverage))
    for name in sorted(n for n in dir(EdgeAndFuzzCases) if n.startswith(("test_edge_", "test_fuzz_seed_"))):
        suite.addTest(EdgeAndFuzzCases(name))
    for name in ("test_precision_mismatch_is_detected", "test_line_order_mismatch_is_detected",
                 "test_tracking_mismatch_is_detected", "test_changed_input_is_detected"):
        suite.addTest(NegativeControl(name))
    suite.addTests(loader.loadTestsFromTestCase(FixtureAgreesWithLiveV1))
    suite.addTests(loader.loadTestsFromTestCase(InvalidV1Root))
    return suite


class InvalidV1Root(unittest.TestCase):
    """A BA1_ROOT that is set but is not a 1.0 clone is an error, not a quiet switch to the
    stored results (and not a skip in the tests that rebuild machines)."""

    def test_set_but_wrong_ba1_root_raises(self):
        from . import support
        saved = (v1_harness.V1_ROOT, dict(v1_harness._STATE), support.BA1_ROOT)
        v1_harness.V1_ROOT = support.BA1_ROOT = str(Path(tempfile.mkdtemp(prefix="not_v1_")))
        v1_harness._STATE.clear()
        try:
            with self.assertRaisesRegex(RuntimeError, "BA1_ROOT must point at a BuildArena 1.0"):
                type("Probe", (ScoringEquivalence,), {}).setUpClass()
            with self.assertRaisesRegex(RuntimeError, "BA1_ROOT must point at a BuildArena 1.0"):
                support.paths()
        finally:
            v1_harness.V1_ROOT, support.BA1_ROOT = saved[0], saved[2]
            v1_harness._STATE.clear()
            v1_harness._STATE.update(saved[1])


if __name__ == "__main__":
    unittest.main()
