"""The machine prepared for evaluation must be the one the MCP saved at the end of
the build session (README section 5). Two 2.0 tools can raise
after changing the machine, and a call that raises is in no history file:
removing a connector (build.py:1246-1248) and rolling back a shift whose offsets
are strings (build.py:1413)."""

from __future__ import annotations

import functools
import json
import shutil
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from . import support
from .test_sample import make_sample
from buildarena1.evalkit import stepwise
from buildarena1.evalkit.config import EvalConfig
from buildarena1.evalkit.rebuild import (MachineMismatch, RebuildError, _pair_blocks, check_final, mcp_differences, prepare,
                             rebuild, same_block)

BRACE = support.CAR + [("connect_blocks", {"block_a": 2, "face_a": "f2", "block_b": 3, "face_b": "f2",
                                           "connector": "Brace"}),
                       ("remove_block", {"block_id": 8})]               # raises after deleting the Brace
STUCK = support.CAR + [("shift_block", {"block_id": "4", "shift_real": ["0", "1", "0"]})]   # into wheel 5, rollback raises
HARMLESS = support.CAR + [("remove_block", {"block_id": 9}),                       # "not found", returned normally
                          ("shift_block", {"block_id": "4", "shift_real": [0, 1, 0]}),   # collides, rolled back
                          ("list_blocks_by_category", {"basic": ""})]               # argument validation error


def session(calls, root: Path, name: str) -> Path:
    return support.mcp_session(calls, root / name / "machine", name)


def prep(history: Path, out: Path, **kw):
    return prepare(history, "transport", "soft", EvalConfig(), out, history.stem,
                   mcp_bsg=history.with_suffix(".bsg"), tool_calls=history.parents[1] / "tool_calls.jsonl", **kw)


def positions(bsg: Path, blocks: list[dict]) -> dict[str, list[float]]:
    by_guid = {b.get("guid"): b for b in ET.parse(bsg).getroot().findall("Blocks/Block")}
    return {r["build_id"]: [float(by_guid[r["guid"]].find("Transform/Position").get(k)) for k in "xyz"]
            for r in blocks if r["build_id"] is not None}


class TheTwoBugs(unittest.TestCase):
    """The sessions reproduce what the build stage saw, and the valid history alone misses it."""

    @classmethod
    def setUpClass(cls):
        support.v2_ready()
        cls.root = support.scratch("mcheck_")
        cls.brace = session(BRACE, cls.root, "brace")
        cls.stuck = session(STUCK, cls.root, "stuck")
        cls.harmless = session(HARMLESS, cls.root, "harmless")

    def calls(self, history: Path) -> list[dict]:
        return [json.loads(l) for l in (history.parents[1] / "tool_calls.jsonl").read_text().splitlines()]

    def test_sessions_raise_like_the_build_stage(self):
        self.assertEqual(self.calls(self.brace)[-1]["result"], "Error executing tool remove_block: 8 not in collision manager!")
        self.assertEqual(self.calls(self.stuck)[-1]["result"],
                         "Error executing tool shift_block: could not convert string to float: ''")
        mcp = ET.parse(self.brace.with_suffix(".bsg")).getroot().findall("Blocks/Block")
        self.assertNotIn("7", [b.get("id") for b in mcp])                 # the MCP's machine has no Brace
        self.assertEqual(json.loads(self.brace.read_text())[-1]["op"], "connect_blocks")   # the removal is not recorded

    def test_valid_history_alone_differs_from_the_mcp(self):
        for history, extra in ((self.brace, [("8", "7")]), (self.stuck, [("4", "2")])):
            out = self.root / f"{history.stem}_plain"
            p = prepare(history, "transport", "soft", EvalConfig(), out, history.stem)   # no check
            self.assertEqual(p.machine_check, {"status": "not_checked"})
            diff = mcp_differences(p.bsg, p.blocks, history.with_suffix(".bsg"), EvalConfig())
            self.assertEqual([(d["build_id"], d["type"]) for d in diff["not_in_mcp"]], extra)


class Corrected(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.v2_ready()
        cls.root = support.scratch("mcheck_fix_")
        cls.brace = session(BRACE, cls.root, "brace")
        cls.stuck = session(STUCK, cls.root, "stuck")

    def test_brace_removed_and_build_ids_kept(self):
        p = prep(self.brace, self.root / "brace_out")
        self.assertEqual(p.machine_check["status"], "corrected")
        self.assertEqual([r["build_id"] for r in p.blocks], ["1", "2", "3", "4", "5", "6", "7"])
        fix = p.manifest["machine_corrected"]
        self.assertEqual(fix["valid_history_replay"]["not_in_mcp"][0]["build_id"], "8")
        self.assertEqual([(c["seq"], c["tool"], c["removed"]) for c in fix["raised_calls_that_changed_the_machine"]],
                         [(10, "remove_block", ["8"])])
        self.assertIsNone(mcp_differences(p.bsg, p.blocks, self.brace.with_suffix(".bsg"), EvalConfig()))
        self.assertEqual(sorted(p.actions), ["4", "5", "6", "7"])            # control bindings by Build ID
        written = json.loads((self.root / "brace_out" / "block_map.json").read_text())
        self.assertEqual(written["machine_corrected"], fix)

    def test_stuck_shift_kept_where_the_mcp_left_it(self):
        p = prep(self.stuck, self.root / "stuck_out")
        self.assertEqual(p.machine_check["status"], "corrected")
        plain = prepare(self.stuck, "transport", "soft", EvalConfig(), self.root / "stuck_plain", "stuck")
        moved, before = positions(p.bsg, p.blocks), positions(plain.bsg, plain.blocks)
        self.assertEqual([k for k in moved if moved[k] != before[k]], ["4"])
        self.assertAlmostEqual(moved["4"][2] - before["4"][2], 1.0)          # real forward = virtual z
        changed = p.manifest["machine_corrected"]["raised_calls_that_changed_the_machine"]
        self.assertEqual([(c["tool"], c["changed"]) for c in changed], [("shift_block", ["4"])])


class Unchanged(unittest.TestCase):
    """A machine the valid history rebuilds right is exported as before the check."""

    def test_harmless_errors_and_reordering_leave_the_export_alone(self):
        support.v2_ready()
        root = support.scratch("mcheck_same_")
        history = session(HARMLESS, root, "harmless")
        mcp = [[float(b.find("Transform/Position").get(k)) for k in "xyz"]
               for b in ET.parse(history.with_suffix(".bsg")).getroot().findall("Blocks/Block")]
        # the failed shift put wheel 4 last in the MCP's machine.blocks, and its rollback left it 1 ulp off
        self.assertEqual(mcp[-1], [1.0, 0.0, -0.4999999999999999])
        self.assertNotEqual(mcp[3][2], mcp[-1][2])
        checked = prep(history, root / "checked")
        plain = prepare(history, "transport", "soft", EvalConfig(), root / "plain", history.stem)
        self.assertEqual(checked.machine_check["status"], "same")
        self.assertNotIn("machine_corrected", checked.manifest)
        for name in ("harmless_eval.bsg", "machine_state.txt", "replay.log"):
            self.assertEqual((root / "checked" / name).read_bytes(), (root / "plain" / name).read_bytes(), name)
        a, b = (json.loads((root / d / "block_map.json").read_text()) for d in ("checked", "plain"))
        self.assertEqual({k: v for k, v in a.items() if k != "bsg"}, {k: v for k, v in b.items() if k != "bsg"})


class Refused(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.v2_ready()
        cls.root = support.scratch("mcheck_bad_")

    def brace_session(self, name: str) -> Path:
        return session(BRACE, self.root, name)

    def test_no_tool_calls(self):
        history = self.brace_session("nocalls")
        (history.parents[1] / "tool_calls.jsonl").unlink()
        with self.assertRaises(MachineMismatch) as ctx:
            prep(history, self.root / "nocalls_out")
        self.assertEqual(ctx.exception.details["valid_history_replay"]["not_in_mcp"][0]["build_id"], "8")

    def test_mcp_machine_nobody_can_rebuild(self):
        history = self.brace_session("moved")
        bsg = history.with_suffix(".bsg")
        tree = ET.parse(bsg)
        pos = [b for b in tree.getroot().findall("Blocks/Block") if b.get("id") == "2"][0].find("Transform/Position")
        pos.set("x", repr(float(pos.get("x")) + 0.01))
        tree.write(bsg)
        with self.assertRaises(MachineMismatch) as ctx:
            prep(history, self.root / "moved_out")
        self.assertEqual(len(ctx.exception.details["session_replay"]["not_in_mcp"]), 1)

    def test_raised_call_that_does_not_raise_on_replay(self):
        history = self.brace_session("reraise")
        path = history.parents[1] / "tool_calls.jsonl"
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        rows[-1]["result"] = "Error executing tool remove_block: something else"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        with self.assertRaises(MachineMismatch) as ctx:
            prep(history, self.root / "reraise_out")
        self.assertIn("raised 'something else' in the build session", ctx.exception.details["session_replay"]["error"])

    def test_stepwise_records_the_status_and_runs_nothing(self):
        sample = make_sample(support.scratch("mcheck_step_"), "transport_soft", "s01", support.CAR)
        shutil.rmtree(sample / "machine")
        support.mcp_session(BRACE, sample / "machine", "transport_soft_s01")
        (sample / "tool_calls.jsonl").unlink()
        out = stepwise.prepare(sample, paths=support.v2_ready(), attempt="a1", runner=None)
        self.assertEqual((out["status"], out["next"]), ("machine_mismatch", "done"))
        result = json.loads((sample / "eval" / "a1" / "result.json").read_text())
        self.assertEqual(result["status"], "machine_mismatch")
        self.assertEqual(result["machine_check"]["status"], "machine_mismatch")
        self.assertEqual(result["machine_check"]["valid_history_replay"]["not_in_mcp"][0]["build_id"], "8")


class StepwiseCorrected(unittest.TestCase):
    def test_result_and_block_map_carry_the_correction(self):
        sample = make_sample(support.scratch("mcheck_stepfix_"), "transport_soft", "s01", support.CAR)
        shutil.rmtree(sample / "machine")
        support.mcp_session(BRACE, sample / "machine", "transport_soft_s01")
        out = stepwise.prepare(sample, paths=support.v2_ready(), attempt="a1", runner=None)
        self.assertEqual(out["status"], "prepared")
        state = json.loads((sample / "eval" / "a1" / "state.json").read_text())
        self.assertEqual(state["result"]["machine_check"]["status"], "corrected")
        block_map = json.loads((sample / "eval" / "a1" / "prepared" / "block_map.json").read_text())
        self.assertIn("machine_corrected", block_map)
        self.assertEqual(state["result"]["num_blocks"], 7)

    def test_plain_sample_says_same(self):
        sample = make_sample(support.scratch("mcheck_stepsame_"), "lift_soft", "s01", support.ROCKET)
        stepwise.prepare(sample, paths=support.v2_ready(), attempt="a1", runner=None)
        state = json.loads((sample / "eval" / "a1" / "state.json").read_text())
        self.assertEqual(state["result"]["machine_check"]["status"], "same")


class BlockComparison(unittest.TestCase):
    def block(self, **kw):
        base = {"type": "7", "position": [1.0, -2.0, 0.5], "rotation": [0.0, 0.0, -0.7071067811865475, 0.7071067811865476],
                "scale": [1.0, 1.0, 1.0], "data": {"end-position": [-1.0, 0.0, 0.0], "start-rotation": [0.0, 0.0, 0.0]}}
        base.update(kw)
        return base

    def test_connector_euler_angles_compare_as_rotations(self):
        # pairs seen between the MCP's BSG and a replay of the same braces on another host (two real machines)
        for a, b in (([189.73561031724535, 0.0, -9.735610317245342], [88.8645858521806, 0.0, 91.1354141478194]),
                     ([237.52880770915152, 179.99999999999997, 57.52880770915149], [585.0, 179.99999999999997, 45.0]),
                     ([482.4711922908485, 179.99999999999997, 122.47119229084852], [135.0, 179.99999999999997, -225.0])):
            self.assertTrue(same_block(self.block(data={"start-rotation": a}), self.block(data={"start-rotation": b})))
        self.assertFalse(same_block(self.block(data={"start-rotation": [10.0, 0.0, 0.0]}),
                                    self.block(data={"start-rotation": [20.0, 0.0, 0.0]})))
        self.assertFalse(same_block(self.block(data={"start-rotation": [10.0, 90.0, 0.0]}),
                                    self.block(data={"start-rotation": [0.0, 90.0, 10.0]})))

    def test_tolerance_and_quaternion_sign(self):
        self.assertTrue(same_block(self.block(), self.block(rotation=[0.0, 0.0, 0.7071067811865475, -0.7071067811865476])))
        self.assertTrue(same_block(self.block(), self.block(position=[1.0 + 4e-16, -2.0, 0.5])))
        self.assertFalse(same_block(self.block(), self.block(position=[1.001, -2.0, 0.5])))
        self.assertFalse(same_block(self.block(), self.block(type="15")))
        self.assertFalse(same_block(self.block(), self.block(data={"end-position": [-1.0, 0.0, 0.0]})))


class NativeExceptions(unittest.TestCase):
    """2.0 raising inside a replay is a failed replay, not an escaping exception."""

    @classmethod
    def setUpClass(cls):
        support.v2_ready()
        cls.root = support.scratch("mcheck_native_")
        cls.brace = session(BRACE, cls.root, "brace")

    @staticmethod
    def raise_once(name: str):
        """Patch Machine.<name> so its first call raises ValueError, the later ones run."""
        from buildarena.build import Machine
        original = getattr(Machine, name)
        calls = []

        @functools.wraps(original)
        def wrapper(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("boom")
            return original(self, *args, **kwargs)
        return mock.patch.object(Machine, name, wrapper)

    def test_replay_that_raises_goes_on_to_the_session_replay(self):
        with self.raise_once("attach_block_to"):
            p = prep(self.brace, self.root / "native_ok")
        self.assertEqual(p.machine_check["status"], "corrected")
        first = p.manifest["machine_corrected"]["valid_history_replay"]
        self.assertEqual(first, {"error": "History operation 1 (attach_block_to) raised ValueError: boom"})
        self.assertEqual([r["build_id"] for r in p.blocks], ["1", "2", "3", "4", "5", "6", "7"])

    def test_rebuild_names_the_operation_and_chains_the_exception(self):
        with self.raise_once("attach_block_to"):
            with self.assertRaises(RebuildError) as ctx:
                rebuild(self.brace, work_dir=self.root / "native_rb", machine_name="brace")
        self.assertIn("History operation 1 (attach_block_to) raised ValueError: boom", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, ValueError)

    def test_exception_in_both_replays_is_rebuild_failed_not_an_escape(self):
        from buildarena.build import Machine
        original = Machine.attach_block_to

        @functools.wraps(original)
        def always(self, *args, **kwargs):
            raise ValueError("boom")
        with mock.patch.object(Machine, "attach_block_to", always):
            with self.assertRaises(RebuildError) as ctx:
                prep(self.brace, self.root / "native_both")
        self.assertNotIsInstance(ctx.exception, MachineMismatch)
        self.assertIn("Session replay:", str(ctx.exception))


class PairingAndRegistry(unittest.TestCase):
    """The block comparison is a one-to-one pairing whatever the order, and the
    evaluation BSG holds nothing but the registered blocks."""

    def block(self, x):
        return {"type": "15", "position": [x, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0],
                "data": {}}

    def test_pairing_does_not_depend_on_the_order(self):
        # ours at +-0.75e-6, the MCP's at 0 and 1.5e-6: only -0.75e-6 -> 0 and 0.75e-6 -> 1.5e-6 pairs both
        ours = [self.block(0.75e-6), self.block(-0.75e-6)]
        mine = [self.block(0.0), self.block(1.5e-6)]
        self.assertTrue(same_block(ours[0], mine[0]) and same_block(ours[1], mine[0]) and same_block(ours[0], mine[1]))
        self.assertFalse(same_block(ours[1], mine[1]))       # first-fit gives mine[0] to ours[0] and strands mine[1]
        for theirs in (mine, mine[::-1]):
            self.assertEqual(_pair_blocks(ours, theirs), ([], []))
        self.assertEqual(_pair_blocks(ours, [self.block(0.0), self.block(5.0)]), ([1], [1]))

    def test_unpairable_blocks_are_reported_on_both_sides(self):
        ours = [self.block(0.0), self.block(1.0)]
        self.assertEqual(_pair_blocks(ours, [self.block(1.0), self.block(2.0), self.block(0.0)]), ([], [1]))
        self.assertEqual(_pair_blocks(ours, [self.block(0.0)]), ([1], []))

    def make(self, name: str):
        root = support.scratch("mcheck_reg_")
        history = session(support.CAR, root, name)
        p = prepare(history, "transport", "soft", EvalConfig(), root / "out", name)
        return root, history, p

    def tamper(self, p, fn):
        tree = ET.parse(p.bsg)
        fn(tree.getroot().find("Blocks"))
        tree.write(p.bsg)

    def diff(self, history, p, extra=()):
        return mcp_differences(p.bsg, p.blocks, history.with_suffix(".bsg"), EvalConfig(), extra_guids=extra)

    def test_extra_block_without_a_table_row_is_a_difference(self):
        support.v2_ready()
        root, history, p = self.make("extra")
        self.assertIsNone(self.diff(history, p))
        copy = ET.fromstring(ET.tostring(ET.parse(p.bsg).getroot().find("Blocks")[-1]))   # a Brace-like block of its own guid
        copy.set("guid", "00000000-0000-0000-0000-00000000dead")
        self.tamper(p, lambda blocks: blocks.append(copy))
        diff = self.diff(history, p)
        self.assertEqual([b["guid"] for b in diff["blocks_not_in_the_table"]], ["00000000-0000-0000-0000-00000000dead"])
        self.assertEqual(diff["not_in_mcp"], [])                          # the machine rows themselves still pair
        self.assertIsNone(self.diff(history, p, extra=["00000000-0000-0000-0000-00000000dead"]))   # a bank is allowed

    def test_duplicate_guid_is_a_difference(self):
        support.v2_ready()
        root, history, p = self.make("dup")
        first = ET.parse(p.bsg).getroot().find("Blocks")[0]
        self.tamper(p, lambda blocks: blocks.append(ET.fromstring(ET.tostring(first))))
        self.assertEqual(self.diff(history, p)["duplicate_guids"], [first.get("guid")])

    def test_check_final_refuses_an_unregistered_block_and_moves_the_bsg_aside(self):
        support.v2_ready()
        root = support.scratch("mcheck_final_")
        history = session(support.CAR, root, "final")
        p = prep(history, root / "out")
        check_final(p, EvalConfig())                                       # as exported
        copy = ET.fromstring(ET.tostring(ET.parse(p.bsg).getroot().find("Blocks")[0]))
        copy.set("guid", "00000000-0000-0000-0000-00000000beef")
        self.tamper(p, lambda blocks: blocks.append(copy))
        with self.assertRaises(MachineMismatch) as ctx:
            check_final(p, EvalConfig())
        self.assertEqual(len(ctx.exception.details["final_bsg"]["blocks_not_in_the_table"]), 1)
        self.assertFalse(p.bsg.exists())
        self.assertTrue(p.bsg.with_name(p.bsg.name + ".unverified").is_file())


class SessionEnd(unittest.TestCase):
    """The walk over the session's calls stops at the close that saved the MCP's BSG."""

    def test_raising_calls_after_the_close_are_not_put_back(self):
        support.v2_ready()
        root = support.scratch("mcheck_end_")
        history = session(BRACE, root, "closed")
        path = history.parents[1] / "tool_calls.jsonl"
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        n = len(rows)
        rows.append({"seq": n + 1, "id": "c", "name": "mcp__buildarena__close_machine_lifespan", "input": {},
                     "message_id": "m", "is_error": False, "result": json.dumps({"result": "Saved and closed machine lifespan"})})
        rows.append({"seq": n + 2, "id": "d", "name": "mcp__buildarena__shift_block",
                     "input": {"block_id": "4", "shift_real": ["0", "1", "0"]}, "message_id": "m", "is_error": True,
                     "result": "Error executing tool shift_block: Machine lifespan is not active; call create_machine_lifespan first."})
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        p = prep(history, root / "out")
        self.assertEqual(p.machine_check["status"], "corrected")
        self.assertEqual(p.manifest["machine_corrected"]["raised_calls_replayed"], 1)   # the removal before the close only

    def test_a_session_that_never_closed_is_walked_to_its_end(self):
        support.v2_ready()
        root = support.scratch("mcheck_open_")
        history = session(STUCK, root, "open")          # no close call: the last call is the raising shift
        p = prep(history, root / "out")
        self.assertEqual(p.machine_check["status"], "corrected")


class StatusNames(unittest.TestCase):
    """rebuild_failed: no replay could be made. machine_mismatch: one was made and is not the MCP's."""

    @classmethod
    def setUpClass(cls):
        support.v2_ready()

    def broken_sample(self, with_calls: bool):
        sample = make_sample(support.scratch("mcheck_names_"), "transport_soft", "s01", support.CAR)
        history = sample / "machine" / "transport_soft_s01.json"
        ops = json.loads(history.read_text())
        ops.append({"op": "attach_block_to", "params": {"base_block": 1, "face": "f99", "new_block": "Wheel", "note": None},
                    "result": "x"})
        history.write_text(json.dumps(ops))
        if with_calls:
            support.mcp_session(support.CAR, sample / "machine", "transport_soft_s01")
            history.write_text(json.dumps(ops))
        return sample

    def test_history_that_does_not_replay_and_no_tool_calls(self):
        sample = self.broken_sample(False)
        out = stepwise.prepare(sample, paths=support.v2_ready(), attempt="a1", runner=None)
        self.assertEqual((out["status"], out["next"]), ("rebuild_failed", "done"))
        result = json.loads((sample / "eval" / "a1" / "result.json").read_text())
        self.assertIn("no tool_calls.jsonl", result["error"])
        self.assertNotIn("machine_check", result)
        self.assertEqual(list((sample / "eval" / "a1" / "prepared").glob("*_eval.bsg*")), [])

    def test_replay_that_is_not_the_mcps_machine_is_machine_mismatch(self):
        sample = make_sample(support.scratch("mcheck_names2_"), "transport_soft", "s01", support.CAR)
        shutil.rmtree(sample / "machine")
        support.mcp_session(BRACE, sample / "machine", "transport_soft_s01")
        (sample / "tool_calls.jsonl").unlink()
        out = stepwise.prepare(sample, paths=support.v2_ready(), attempt="a1", runner=None)
        self.assertEqual(out["status"], "machine_mismatch")


class NoUnmatchedBsgLeft(unittest.TestCase):
    def test_refused_machine_leaves_no_evaluation_bsg_and_no_next_step(self):
        support.v2_ready()
        sample = make_sample(support.scratch("mcheck_clean_"), "transport_soft", "s01", support.CAR)
        shutil.rmtree(sample / "machine")
        history = support.mcp_session(BRACE, sample / "machine", "transport_soft_s01")
        tree = ET.parse(history.with_suffix(".bsg"))
        pos = [b for b in tree.getroot().findall("Blocks/Block") if b.get("id") == "2"][0].find("Transform/Position")
        pos.set("x", repr(float(pos.get("x")) + 0.01))
        tree.write(history.with_suffix(".bsg"))
        out = stepwise.prepare(sample, paths=support.v2_ready(), attempt="a1", runner=None)
        self.assertEqual((out["status"], out["next"]), ("machine_mismatch", "done"))
        prepared = sample / "eval" / "a1" / "prepared"
        self.assertEqual([p.name for p in prepared.glob("*.bsg*")], [])
        self.assertFalse((prepared / "block_map.json").exists())
        with self.assertRaises(stepwise.StepError):
            stepwise.controller_input(sample, "a1", prompts=None)


if __name__ == "__main__":
    unittest.main()
