"""Text side of the transport control-agent call.

Two pure functions do the work: ``controller_turn`` assembles one round's
input the way 1.0 spatial/agent.py:330-367 and scheduler/runner.py:180-251 did,
and ``parse_controller_reply`` turns the reply into a validated control plan
(agent.py:376-378 plus the checks evalkit.control applies). No network calls;
the caller sends the messages to the model.

1.0 prompt text is read from the 1.0 tree (levels.yaml, prompt.yaml). The
system prompt is the 1.0 controller text with one added sentence that maps
1.0 block names to 2.0 tool names; the same sentence goes to every model, so
every model gets the same prompt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .control import ControlError, ControlPlan, validate_control
from . import legacy as L

RESET_NOTE = "The control info is reset to default."
REVISE_NOTE = ("Please analyze the motion trajectory of the previous control result and design the new "
               "control configuration and sequence to optimize the task.")
NO_KEYS_NOTE = "No control key has been added to any block yet."


def level_task(v1_root: Path, category: str, level: str) -> str:
    """Task text of a cell, as script/run_construction.py:15-48 finds it."""
    with open(v1_root / "levels.yaml", encoding="utf-8") as handle:
        for doc in yaml.safe_load_all(handle.read()):
            if doc and doc.get("category") == category and doc.get("level") == level:
                return doc.get("task", "")
    raise KeyError(f"No 1.0 task for {category} {level}")


def controller_system_prompt(v1_root: Path, available_blks: str, name_note: str) -> str:
    """prompt.yaml `controller.system_message` with {available_blks} filled in
    (agent.py:28-34), then the 2.0 name note after </available_blocks>."""
    with open(v1_root / "prompt.yaml", encoding="utf-8") as handle:
        template = yaml.safe_load(handle)["controller"]["system_message"]
    text = template.replace("{available_blks}", available_blks)
    anchor = "</available_blocks>\n"
    if text.count(anchor) != 1:
        raise ValueError("Controller prompt does not have exactly one </available_blocks> line.")
    return text.replace(anchor, anchor + name_note.rstrip("\n") + "\n", 1)


_SECTION = re.compile(r"<(functionality|motion_control)>(.*?)</\1>", re.DOTALL)


def plan_sections(final_reply: str) -> tuple[str, str]:
    """<functionality> and <motion_control> from the build session's last reply."""
    found = {name: body.strip() for name, body in _SECTION.findall(final_reply)}
    missing = [n for n in ("functionality", "motion_control") if n not in found]
    if missing:
        raise ValueError(f"Final reply lacks section(s) {missing}.")
    return found["functionality"], found["motion_control"]


def task_content(task_text: str, functionality: str, motion_control: str) -> str:
    """script/run_simulation.py:86-88."""
    plan = "\n".join([functionality, motion_control])
    return f"The task instruction: \n{task_text}\n\n---\n\n The plan of the machine structure: \n{plan}"


def machine_state(summary: str, actions: str) -> str:
    """agent.py:353-355 (current_machine_state)."""
    return "\n".join(["Machine Summary: ", summary, "Available Actions: ", actions, "Control Config: ",
                      NO_KEYS_NOTE])


def controller_reply_message(text: str) -> dict:
    """A controller reply as 1.0 fed it back in the next round.

    1.0 reloads the saved reply as TextMessage(source="controller")
    (spatial/agent.py:358-360); autogen-agentchat 0.7.4 turns every
    TextMessage into a UserMessage, so the model saw its own reply as a user
    message named "controller" (checked with autogen-agentchat 0.7.4).
    """
    return {"role": "user", "name": "controller", "content": text}


def controller_turn(*, task: str, state: str, previous: list[dict] | None = None,
                    feedback: str | None = None) -> list[dict]:
    """Messages of one control round (agent.py:357-367).

    Round 1: the task content and the machine state. Later rounds: every
    message of the previous round including its reply, then the trajectory
    feedback, the reset note, the machine state and the revise request.
    A reply passed in ``previous`` with role "assistant" is re-sent the way
    1.0 sent it (controller_reply_message).
    """
    if previous is None:
        return [{"role": "user", "content": task}, {"role": "user", "content": state}]
    if feedback is None:
        raise ValueError("A revision round needs the trajectory feedback.")
    carried = [controller_reply_message(m["content"]) if m.get("role") == "assistant" else dict(m)
               for m in previous]
    return [*carried, {"role": "user", "content": feedback}, {"role": "user", "content": RESET_NOTE},
            {"role": "user", "content": state}, {"role": "user", "content": REVISE_NOTE}]


def parse_controller_reply(text: str, actions: dict[str, tuple[str, ...]], block_ids: set[str]) -> tuple[dict, ControlPlan]:
    """Reply -> (raw control JSON, validated plan). Raises ControlError where 1.0 would fail.

    1.0 took the text between the first ```json and the next ``` (agent.py:376-378).
    """
    try:
        body = text.split("```json")[1].split("```")[0]
    except IndexError as exc:
        raise ControlError("Reply has no ```json block.") from exc
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ControlError(f"Reply JSON does not parse: {exc}") from exc
    return data, validate_control(data, actions, block_ids)


# ---------------------------------------------------------------- machine side
def powered_actions(machine) -> dict[str, tuple[str, ...]]:
    """Build ID -> 1.0 actions for blocks 1.0 treated as powered."""
    out = {}
    for build_id, block in machine.blocks.items():
        name = L.V2_TO_V1_NAME.get(block.name)
        if name in L.V1_ACTIONS:
            out[str(build_id)] = L.V1_ACTIONS[name]
    return out


def powered_blocks_text(machine) -> str:
    """Stand-in for 1.0 review_powered_blocks (build.py:1035-1048, 229-238);
    2.0 has no such tool, so its block caption is used with 1.0's layout."""
    lines = ["The machine has the following powered blocks:"]
    for build_id, actions in powered_actions(machine).items():
        lines.append(machine.blocks[build_id].caption(finished=True))
        lines.append("Controllable Locomotion: ")
        lines.extend(f"Action: {action}, Key: " for action in actions)
    return "\n".join(lines)


def machine_state_from(machine) -> str:
    """Machine state text for a freshly rebuilt 2.0 machine (call before export:
    2.0 Machine.to_xml moves the Starting Block to the end of machine.blocks)."""
    machine.output_sequence_path = None
    return machine_state(machine.get_machine_summary(), powered_blocks_text(machine))


@dataclass(frozen=True)
class ControllerPrompts:
    system: str
    v1_root: Path

    def task(self, category: str, level: str) -> str:
        return level_task(self.v1_root, category, level)
