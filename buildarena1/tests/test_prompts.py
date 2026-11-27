"""Control-agent text: system prompt, turns, reply parsing, trajectory feedback."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from . import v1_harness
from buildarena1.evalkit.control import ControlError
from buildarena1.evalkit.legacy_log import trajectory_feedback
from buildarena1.evalkit.prompts import (RESET_NOTE, REVISE_NOTE, controller_reply_message, controller_system_prompt,
                             controller_turn, level_task, parse_controller_reply, plan_sections, task_content)

NOTE = 'In the BuildArena tools, Small Wooden Block is named "Single Wooden Block".'
REPLY = """Here is the plan.
```json
{"control_design": "drive east",
 "control_config": [{"key": "Alpha1", "action": "spinning_forward", "block_id": 4}],
 "control_sequence": [{"motion_action": "go", "time": 0.5, "key": "Alpha1", "hold_for": 20}]}
```
trailing words"""


class Prompts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v1 = v1_harness.enter()
        cls.root = Path(v1_harness.V1_ROOT)
        cls.blks = cls.v1["build"].Blocks()()

    def test_system_prompt_is_1_0_text_plus_note(self):
        ours = controller_system_prompt(self.root, self.blks, NOTE)
        template = yaml.safe_load((self.root / "prompt.yaml").read_text(encoding="utf-8"))["controller"]["system_message"]
        v1_text = template.replace("{available_blks}", self.blks)      # spatial/agent.py:33
        self.assertEqual(ours.replace(NOTE + "\n", "", 1), v1_text)
        self.assertIn("</available_blocks>\n" + NOTE + "\n", ours)

    def test_level_task_is_levels_yaml(self):
        docs = [d for d in yaml.safe_load_all((self.root / "levels.yaml").read_text(encoding="utf-8")) if d]
        self.assertEqual(len(docs), 9)
        for doc in docs:
            self.assertEqual(level_task(self.root, doc["category"], doc["level"]), doc["task"])
        with self.assertRaises(KeyError):
            level_task(self.root, "transport", "extreme")

    def test_turns(self):
        task = task_content("TASK\n", "FUNC", "MOTION")
        self.assertEqual(task, "The task instruction: \nTASK\n\n\n---\n\n The plan of the machine structure: \nFUNC\nMOTION")
        first = controller_turn(task=task, state="STATE")
        self.assertEqual(first, [{"role": "user", "content": task}, {"role": "user", "content": "STATE"}])
        previous = first + [{"role": "assistant", "content": REPLY}]
        second = controller_turn(task=task, state="STATE", previous=previous, feedback="TRAJ")
        # 1.0 re-sends the reply as TextMessage(source="controller"), which autogen 0.7.4
        # turns into a user message (spatial/agent.py:358-360).
        self.assertEqual(second[:3], first + [{"role": "user", "name": "controller", "content": REPLY}])
        self.assertEqual(second[:3], first + [controller_reply_message(REPLY)])
        self.assertNotIn("assistant", [m["role"] for m in second])
        third = controller_turn(task=task, state="STATE", previous=second + [{"role": "assistant", "content": "R2"}],
                                feedback="TRAJ2")
        self.assertEqual(third[:7], second)
        self.assertEqual(third[7], controller_reply_message("R2"))
        self.assertEqual(len(third), 12)
        self.assertEqual([m["content"] for m in second[3:]], ["TRAJ", RESET_NOTE, "STATE", REVISE_NOTE])
        with self.assertRaises(ValueError):
            controller_turn(task=task, state="STATE", previous=previous)

    def test_plan_sections(self):
        text = "done\n<functionality>\n wheels push \n</functionality>\n<motion_control>hold Alpha1</motion_control>"
        self.assertEqual(plan_sections(text), ("wheels push", "hold Alpha1"))
        with self.assertRaises(ValueError):
            plan_sections("<functionality>x</functionality>")

    def test_parse_reply(self):
        raw, plan = parse_controller_reply(REPLY, {"4": ("spinning_forward", "spinning_backward")}, {"1", "4"})
        self.assertEqual(raw["control_design"], "drive east")
        self.assertEqual([(p.time, p.key, p.hold_for) for p in plan.sequence], [(0.5, "Alpha1", 20.0)])
        for bad in ("no code block", "```json\n{not json}\n```", "```json\n[1, 2]\n```"):
            with self.assertRaises(ControlError, msg=bad):
                parse_controller_reply(bad, {"4": ("spinning_forward",)}, {"1", "4"})

    def test_parse_takes_the_first_json_block(self):
        two = REPLY + '\n```json\n{"control_config": [], "control_sequence": []}\n```'
        _, plan = parse_controller_reply(two, {"4": ("spinning_forward", "spinning_backward")}, {"1", "4"})
        self.assertEqual(len(plan.bindings), 1)


class Feedback(unittest.TestCase):
    ROWS = [["0.0", "ID_1", "0.0", "5.0", "0.0"], ["0.0", "ID_Ballast", "5.2"],
            ["0.0", "ID_Ballast", "0.0", "8.0", "0.0"], ["0.5", "ID_1", "9.0", "9.0", "9.0"],
            ["1.0", "ID_1", "1.0", "5.5", "2.0"], ["1.0", "ID_Ballast", "1.0", "7.0", "2.5"]]

    def test_matches_runner_py_layout(self):
        """scheduler/runner.py:202-228: whole seconds only, 5-column rows, offsets
        from the first kept row, y printed from the vertical column."""
        text, not_moving = trajectory_feedback(self.ROWS)
        self.assertEqual(text.splitlines(), [
            "The simulation trajectory (time, Block ID, x, y, z): ",
            "t=0.0, ID=ID_1, x=0.00, y=0.00, z=0.00",
            "t=0.0, ID=Cargo Load, x=0.00, y=3.00, z=0.00",
            "t=1.0, ID=ID_1, x=1.00, y=0.50, z=2.00",
            "t=1.0, ID=Cargo Load, x=1.00, y=2.00, z=2.50"])
        self.assertFalse(not_moving)
        task_axes, _ = trajectory_feedback(self.ROWS, axis_labels="task")
        self.assertIn("t=1.0, ID=ID_1, x=1.00, y=2.00, z=0.50", task_axes)

    def test_not_moving(self):
        still = [["0.0", "ID_1", "0.0", "5.0", "0.0"], ["1.0", "ID_1", "0.5", "5.2", "0.3"]]
        self.assertTrue(trajectory_feedback(still)[1])
        with self.assertRaises(ValueError):
            trajectory_feedback([["0.5", "ID_1", "0.0", "5.0", "0.0"]])


if __name__ == "__main__":
    unittest.main()
