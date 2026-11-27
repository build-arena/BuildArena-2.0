"""How each build-session task is assembled: which parts are new text and which are copied
from BuildArena 1.0, and the exact edits made to the copied parts.

The task generator (gen_tasks.py) builds the task files from this spec, and the checker
(check_tasks.py) uses the same spec to re-derive every copied part from 1.0 and confirm the
only differences from the source are the edits listed here. Section 2 of
buildarena1/README.md describes the result.

A segment is either
  {"kind": "new", "text": ...}                 written for this project, or
  {"kind": "ba1", "source": ..., "edits": [...]} copied from 1.0; `edits` is the complete
                                               list of (old, new) replacements, each `old`
                                               must occur exactly once in the source text.
"""
from __future__ import annotations

from buildarena1.tasks import ba1_source as src

BLOCKSETS = ("ba1_blocks",)
LEVEL_LABEL = {"soft": "Lv.1", "medium": "Lv.2", "hard": "Lv.3"}

ONE_MACHINE = (
    "- Build everything as one machine in a single build session. "
    "Sub-structures cannot be built separately and assembled in this environment."
)
# Sub-structure constraints that 2.0 cannot honour: it has no assembly tools.
USER_EDITS = {
    ("support", "medium"): [("- Use no more than 3 sub-structures.", ONE_MACHINE)],
    ("support", "hard"): [("- Use no more than 3 sub-structures.", ONE_MACHINE)],
    ("lift", "hard"): [("- Use only two sub-structures.", ONE_MACHINE)],
}

HEADER = (
    "You build machines in a physics simulator (Besiege) through the BuildArena MCP tools. "
    "You work alone in this session: you design the machine, build it with the tools, "
    "and check it with the query tools.\n"
    "\n"
    "How to use the tools\n"
    '- Call create_machine_lifespan(machine_name="{machine_name}") exactly once, then call start() exactly once.\n'
    "- Build with attach_block_to and connect_blocks. Adjust with twist_block, shift_block and flip_spin. "
    "Inspect with get_machine_summary and get_block_details.\n"
    "- When the machine is finished, call close_machine_lifespan() with no arguments. "
    "The machine saved at that moment is the machine that will be tested.\n"
)
# The prompts do not mention load_machine_from_history: the build session does not offer
# that tool to the model (it would load a whole machine from a file), so the prompt would name
# a tool the model cannot see. check_tasks.py asserts it stays out.
FORBIDDEN_IN_PROMPTS = ("load_machine_from_history",)
TRANSPORT_REPLY = (
    "- After closing the machine, reply with the two sections below and nothing else. "
    "A control engineer will use them to write the control sequence.\n"
)
NAME_MAP = (
    'In the BuildArena tools, Small Wooden Block is named "Single Wooden Block", '
    'Powered Wheel is named "Wheel", and Winch is named "Rope Winch".'
)
BLOCKSET_TAIL = {
    "ba1_blocks": " No other blocks are available.",
}

YAML_INDENT = 6  # planner.system_message is a block scalar indented 6 spaces in prompt.yaml


def prompt_lines(first: int, last: int, *, dedent: int | None = YAML_INDENT) -> str:
    """prompt.yaml lines first..last (1-based, inclusive) joined with LF, plus a final LF.
    dedent=N removes the YAML block indentation (asserting it is all spaces);
    dedent=None strips all leading whitespace from each line."""
    lines = src.prompt_yaml_lines()[first:last + 1]
    out = []
    for line in lines:
        if dedent is None:
            out.append(line.lstrip())
        else:
            if line.strip() and line[:dedent] != " " * dedent:
                raise ValueError(f"prompt.yaml line not indented {dedent}: {line!r}")
            out.append(line[dedent:])
    return "\n".join(out) + "\n"


def ba1_segment_text(seg: dict) -> str:
    """Re-derive a copied segment's text from 1.0 and apply its edits."""
    s = seg["source"]
    if s["file"] == "prompt.yaml":
        text = "".join(prompt_lines(a, b, dedent=s.get("dedent", YAML_INDENT)) for a, b in s["lines"])
    elif s["file"] == "levels.yaml":
        text = src.level_task(s["category"], s["level"])
    else:
        raise ValueError(s)
    for old, new in seg["edits"]:
        if text.count(old) != 1:
            raise ValueError(f"edit target occurs {text.count(old)} times: {old[:60]!r}")
        if new == "<<1.0 Blocks()() output>>":
            new = src.blocks_call_output()
        text = text.replace(old, new)
    return text


def system_segments(category: str, level: str, blockset: str) -> list[dict]:
    machine_name = src.cell_name(category, level)
    segs: list[dict] = [{"kind": "new", "text": HEADER.replace("{machine_name}", machine_name)}]
    if category == "transport":
        segs.append({"kind": "new", "text": TRANSPORT_REPLY})
        # planner output format, <functionality> and <motion_control> only: the control agent reads them
        segs.append({
            "kind": "ba1",
            "source": {"file": "prompt.yaml", "lines": [[43, 45], [49, 51]], "dedent": None,
                       "note": "planner output format: <functionality> and <motion_control>, leading whitespace removed"},
            "edits": [],
        })
    segs.append({"kind": "new", "text": "\nEnvironment\n"})
    segs.append({
        "kind": "ba1",
        "source": {"file": "prompt.yaml", "lines": [[16, 22]],
                   "note": "planner.system_message: starting block and coordinate convention"},
        "edits": [
            # one machine per session, no sub-structures
            [" as the base of each individual building process for each sub-structure.",
             " as the base of each individual building process."],
            ["so make sure your plan for each sub-structure includes the base block.",
             "so make sure your plan includes the base block."],
        ],
    })
    segs.append({"kind": "new", "text": "\n"})
    segs.append({
        "kind": "ba1",
        "source": {"file": "prompt.yaml", "lines": [[33, 34]],
                   "note": "planner.system_message: plan steps 7 and 8 (attachment rules)"},
        "edits": [["7. The structures", "- The structures"], ["8. The attachment", "- The attachment"]],
    })
    segs.append({"kind": "new", "text": "\n"})
    segs.append({
        "kind": "ba1",
        "source": {"file": "prompt.yaml", "lines": [[11, 14]],
                   "note": "planner.system_message: block list; {available_blks} is replaced by 1.0's Blocks()() "
                           "exactly as 1.0 spatial/agent.py:26-34 did"},
        "edits": [["{available_blks}", "<<1.0 Blocks()() output>>"]],
    })
    segs.append({"kind": "new", "text": NAME_MAP + BLOCKSET_TAIL[blockset]})
    return segs


def user_segments(category: str, level: str) -> list[dict]:
    return [{
        "kind": "ba1",
        "source": {"file": "levels.yaml", "category": category, "level": level,
                   "note": "the cell's `task` field"},
        "edits": [list(e) for e in USER_EDITS.get((category, level), [])],
    }]


def render(segs: list[dict]) -> list[dict]:
    out = []
    for seg in segs:
        seg = dict(seg)
        if seg["kind"] == "ba1":
            seg["text"] = ba1_segment_text(seg)
        out.append(seg)
    return out
