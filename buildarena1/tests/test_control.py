"""Control JSON -> channel schedule, the runtime controller, the lift timeline."""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
import types
import unittest
from pathlib import Path

from . import support
from buildarena1.evalkit import legacy as L
from buildarena1.evalkit.control import (ControlError, ControlPlan, Binding, Press, channel_schedule, key_intervals,
                             validate_control, write_lift_timeline, write_python_controller)

ACTIONS = {"4": ("spinning_forward", "spinning_backward"), "5": ("spinning_forward", "spinning_backward"),
           "6": ("hold_to_fire",)}
IDS = {"1", "2", "3", "4", "5", "6"}


def lua_oracle(entries, duration):
    """Frame-by-frame replay of the 1.0 Lua loop (build.py:1281-1298) for one key."""
    held, state = [], False
    for frame in range(1, int(round(duration * 100)) + 1):
        t = frame / 100
        called = None
        for s, f in entries:
            if t >= s:
                called = True
            if t >= f:
                called = False
        state = state if called is None else called
        held.append(state)
    intervals, start = [], None
    for frame, on in enumerate(held, start=1):
        t = (frame - 1) / 100   # state set in frame n holds over [t_(n-1), t_n)
        if on and start is None:
            start = t
        if not on and start is not None:
            intervals.append((start, t)); start = None
    if start is not None:
        intervals.append((start, duration))
    return intervals


def close(a, b, tol=0.0101):
    return len(a) == len(b) and all(abs(x[0] - y[0]) <= tol and abs(x[1] - y[1]) <= tol for x, y in zip(a, b))


class Validation(unittest.TestCase):
    def test_valid_and_dropped_entries(self):
        data = {"control_config": [
            {"key": "Alpha1", "action": "spinning_forward", "block_id": 4},
            {"key": "Alpha1", "action": "spinning_forward", "block_id": "5"},
            {"key": "Space", "action": "spinning_forward", "block_id": 4},
            {"key": "Alpha2", "action": "spinning_forward", "block_id": 9},
            {"key": "Alpha3", "action": "spinning_forward", "block_id": 2},
            {"key": "Alpha4", "action": "hold_to_fire", "block_id": 4},
            {"key": "Keypad0", "action": "hold_to_fire", "block_id": 6}],
            "control_sequence": [{"time": 0.5, "key": "Alpha1", "hold_for": 10},
                                 {"time": 1, "key": "Alpha9", "hold_for": 1},
                                 {"time": "soon", "key": "Alpha2", "hold_for": 1},
                                 {"time": 2, "key": "Keypad0", "hold_for": 3}]}
        plan = validate_control(data, ACTIONS, IDS)
        self.assertEqual(plan.bindings, [Binding("4", "spinning_forward", "Alpha1"),
                                         Binding("5", "spinning_forward", "Alpha1"),
                                         Binding("6", "hold_to_fire", "Keypad0")])
        self.assertEqual(plan.sequence, [Press(0.5, "Alpha1", 10.0), Press(2.0, "Keypad0", 3.0)])
        reasons = [d["reason"] for d in plan.dropped]
        self.assertEqual(reasons[:4], ["key 'Space' is not in the control key list", "block 9 not found",
                                       "block 2 has no locomotion actions",
                                       "action 'hold_to_fire' is not an action of block 4"])
        self.assertEqual(reasons[4:], ["key is not bound", "key is not bound"])

    def test_fatal_cases(self):
        good = [{"key": "Alpha1", "action": "spinning_forward", "block_id": 4}]
        seq = [{"time": 0, "key": "Alpha1", "hold_for": 1}]
        for data in ({"control_config": [], "control_sequence": seq},
                     {"control_config": good, "control_sequence": []},
                     {"control_config": good},
                     {"control_config": [{"key": "Alpha1"}], "control_sequence": seq},
                     {"control_config": good, "control_sequence": [{"time": "0", "key": "Alpha1", "hold_for": 1}]},
                     {"control_config": [{"key": 1, "action": "spinning_forward", "block_id": 4}],
                      "control_sequence": seq},
                     ["not", "an", "object"]):
            with self.assertRaises(ControlError, msg=repr(data)):
                validate_control(data, ACTIONS, IDS)


class KeyLogic(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(key_intervals([(1.0, 3.0)], 30, "lua_last_started"), [(1.0, 3.0)])
        self.assertEqual(key_intervals([(25.0, 40.0)], 30, "lua_last_started"), [(25.0, 30)])
        self.assertEqual(key_intervals([(5.0, 5.0)], 30, "lua_last_started"), [])
        self.assertEqual(key_intervals([(-1.0, 2.0)], 30, "lua_last_started"), [(0.0, 2.0)])
        nested = [(1.0, 5.0), (2.0, 3.0)]
        self.assertEqual(key_intervals(nested, 30, "lua_last_started"), [(1.0, 3.0)])
        self.assertEqual(key_intervals(nested, 30, "union"), [(1.0, 5.0)])
        self.assertEqual(key_intervals(list(reversed(nested)), 30, "lua_last_started"), [(1.0, 5.0)])
        # A negative hold never presses but its release line still fires (found by the oracle below).
        self.assertEqual(key_intervals([(16.9, 22.5), (19.1, 18.6)], 30, "lua_last_started"), [(16.9, 18.6)])

    def test_matches_frame_by_frame_lua(self):
        rng = random.Random(7)
        for _ in range(300):
            entries = [(round(rng.uniform(-1, 31), 1), 0.0) for _ in range(rng.randint(1, 5))]
            entries = [(s, round(s + rng.uniform(-0.5, 12), 1)) for s, _ in entries]
            ours = key_intervals(entries, 30.0, "lua_last_started")
            self.assertTrue(close(ours, lua_oracle(entries, 30.0)), f"{entries}: {ours} vs {lua_oracle(entries, 30.0)}")


class Schedule(unittest.TestCase):
    def test_keys_map_to_channels(self):
        plan = ControlPlan([Binding("4", "spinning_forward", "Alpha1"), Binding("5", "spinning_backward", "Alpha1"),
                            Binding("4", "spinning_forward", "Alpha2"), Binding("6", "hold_to_fire", "Keypad3")],
                           [Press(1.26, "Alpha1", 2.0), Press(2.0, "Alpha2", 4.0), Press(29.0, "Keypad3", 5.0)])
        got = [(i.build_id, i.channel, i.start, i.end)
               for i in channel_schedule(plan, 30.0, quantize=True, rule="lua_last_started")]
        self.assertEqual(got, [("4", "ForwardKey", 1.3, 6.0), ("5", "BackwardKey", 1.3, 3.3),
                               ("6", "ShootKey", 29.0, 30.0)])
        raw = channel_schedule(plan, 30.0, quantize=False, rule="lua_last_started")
        self.assertEqual((raw[0].start, raw[1].end), (1.26, 1.26 + 2.0))

    def test_one_tenth_rounding_follows_printf(self):
        plan = ControlPlan([Binding("4", "spinning_forward", "Alpha1")], [Press(1.25, "Alpha1", 1.0)])
        interval = channel_schedule(plan, 30.0, quantize=True, rule="lua_last_started")[0]
        self.assertEqual((interval.start, interval.end), (float("%.1f" % 1.25), float("%.1f" % 2.25)))


def load_runtime():
    spec = importlib.util.spec_from_file_location("runtime_controller_under_test",
                                                  Path(L.__file__).with_name("runtime_controller.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Stands in for controller_sdk.ControllerClient: 25 Hz samples to 31 s."""
    instances = []

    def __init__(self):
        self.times = [k * 0.04 for k in range(1, 776)]
        self.i = 0
        self.calls = []
        self._block_guids = {0: "x"}
        FakeClient.instances.append(self)

    @classmethod
    def from_environment(cls, poll_interval=0.05):
        return cls()

    def arm(self):
        self.calls.append(("arm",))

    def _sample(self):
        return types.SimpleNamespace(simulation_time=self.times[self.i])

    def wait_until_running(self, timeout=120.0):
        return self._sample()

    def next_sample(self, timeout=2.0):
        self.i += 1
        return self._sample()

    def send_channels(self, channels):
        self.calls.append(("channels", self.times[self.i], tuple(channels)))

    def send_sliders(self, sliders):
        self.calls.append(("sliders", self.times[self.i], tuple(sliders)))

    def close(self):
        self.calls.append(("close",))


class RuntimeController(unittest.TestCase):
    def test_runs_schedule_and_ramp(self):
        out = support.scratch("runtime_")
        plan = ControlPlan([Binding("4", "spinning_forward", "Alpha1")], [Press(1.0, "Alpha1", 2.0)])
        intervals = channel_schedule(plan, 30.0, quantize=True, rule="lua_last_started")
        controller = write_python_controller(out, duration=30.0, tick_s=0.1, intervals=intervals,
                                             guid_of={"4": "g4"}, ballast_guid="gb", ramp=L.TRANSPORT_RAMP)
        schedule = json.loads((out / "schedule.json").read_text())
        self.assertEqual(schedule["channels"], [{"build_id": "4", "guid": "g4", "channel": "ForwardKey",
                                                 "start": 1.0, "end": 3.0}])
        rt = load_runtime()
        for t in (0.0, 0.3, 0.5, 1.0, 1.7, 2.5, 3.0, 29.9):
            self.assertEqual(rt.cargo_mass(schedule["ballast"], t), L.TRANSPORT_RAMP.mass_at(t))
        sdk = types.ModuleType("controller_sdk"); sdk.ControllerClient = FakeClient
        proto = types.ModuleType("controller_sdk.protocol"); proto.ENV_RUN_DIR = "FAKE_RUN_DIR"
        saved = {k: sys.modules.get(k) for k in ("controller_sdk", "controller_sdk.protocol")}
        sys.modules.update({"controller_sdk": sdk, "controller_sdk.protocol": proto})
        os.environ["FAKE_RUN_DIR"] = str(out)
        try:
            spec = importlib.util.spec_from_file_location("controller_copy", controller)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            self.assertEqual(module.main(), 0)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        calls = FakeClient.instances[-1].calls
        channel_calls = [c for c in calls if c[0] == "channels"]
        self.assertEqual(calls[0], ("arm",))
        self.assertEqual(calls[-1], ("close",))
        self.assertTrue(28 <= len(channel_calls) / 10 <= 31, len(channel_calls))   # about 10 per second
        held = [t for _, t, ch in channel_calls if ch]
        self.assertAlmostEqual(min(held), 1.0, delta=0.05)
        self.assertAlmostEqual(max(held), 2.9, delta=0.05)
        self.assertTrue(all(t < 30.0 for _, t, _ in channel_calls))
        sliders = [c for c in calls if c[0] == "sliders"]
        self.assertTrue(all(s[2][0][:2] == ("gb", "mass") for s in sliders))
        self.assertEqual(sliders[-1][2][0][2], 50.0)
        self.assertEqual(len((out / "controller_trace.jsonl").read_text().splitlines()), len(channel_calls))


class LiftTimeline(unittest.TestCase):
    def test_timeline_reads_as_2_0_v2(self):
        paths = support.v2_ready()
        from besiege_cli.timeline import read_timeline_json
        out = support.scratch("timeline_")
        path = write_lift_timeline(out, ["g1", "g2"], 30.0)
        payload = read_timeline_json(path)
        events = sorted((e["time"], e["block_guid"], e["value"]) for e in payload["events"])
        self.assertEqual(events, [(2.0, "g1", 1.0), (2.0, "g2", 1.0), (30.0, "g1", 0.0), (30.0, "g2", 0.0)])
        self.assertTrue(all(e["channel"] == "ShootKey" for e in payload["events"]))
        with self.assertRaises(ValueError):
            write_lift_timeline(out, [], 30.0)


if __name__ == "__main__":
    unittest.main()
