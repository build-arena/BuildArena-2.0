#!/usr/bin/env python3
"""Check that the committed task files copy BuildArena 1.0 verbatim except for the listed edits.

For every tasks/<blockset>/<cell>.json:
  1. system_prompt and user_message are exactly the concatenation of their segments;
  2. the segment list (sources and edits) is the one in tasks/spec.py, so no edit can be
     added to a file without appearing in the spec;
  3. every segment copied from 1.0 is re-derived from 1.0 at BA1_COMMIT (git show),
     with only its listed edits applied, and must equal the stored text byte for byte;
  4. cross-checks that do not go through the spec: the levels.yaml task text with its edits
     applied equals the user message; every 1.0 block that is not disabled appears as
     `<name> shape: ..., mass: ...` and `Description: <1.0 description>`; the
     <available_blocks> body equals 1.0's own Blocks()() output;
  5. regenerating the file gives identical bytes;
  6. neither prompt names load_machine_from_history (not offered to the model);
  7. the file's path (<blockset>/<cell>.json) agrees with its cell, category, level, level
     label, machine name and blockset fields.
Exit status 0 only if every file passes. Needs BA1_ROOT (see ba1_source.py):
    python -m buildarena1.tasks.check_tasks
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from buildarena1.tasks import ba1_source as src  # noqa: E402
from buildarena1.tasks import gen_tasks, spec  # noqa: E402

HERE = Path(__file__).resolve().parent


def _strip_text(segs: list[dict]) -> list[dict]:
    return [{k: v for k, v in s.items() if k != "text"} if s["kind"] == "ba1" else s for s in segs]


def check_file(path: Path, blocks_out: str, configs: dict) -> list[str]:
    errs: list[str] = []
    task = json.loads(path.read_text(encoding="utf-8"))
    cat, lvl, bset = task["category"], task["level"], task["blockset"]
    sys_segs, user_segs = task["system_segments"], task["user_segments"]
    # the path names the task: <blockset>/<category>_<level>.json, and every field inside agrees
    stem, folder = path.stem, path.parent.name
    if src.cell_name(cat, lvl) != stem or task.get("cell") != stem or task.get("machine_name") != stem:
        errs.append(f"file {folder}/{path.name} holds cell {task.get('cell')!r} "
                    f"({cat}/{lvl}, machine {task.get('machine_name')!r})")
    if bset != folder:
        errs.append(f"file in {folder}/ declares blockset {bset!r}")
    if task.get("level_label") != spec.LEVEL_LABEL.get(lvl):
        errs.append(f"level_label {task.get('level_label')!r} does not match level {lvl!r}")

    if task["system_prompt"] != "".join(s["text"] for s in sys_segs):
        errs.append("system_prompt is not the concatenation of its segments")
    if task["user_message"] != "".join(s["text"] for s in user_segs):
        errs.append("user_message is not the concatenation of its segments")

    want_sys = spec.system_segments(cat, lvl, bset)
    want_user = spec.user_segments(cat, lvl)
    if _strip_text(sys_segs) != json.loads(json.dumps(want_sys)):
        errs.append("system segments (sources/edits/new text) differ from spec.py")
    if _strip_text(user_segs) != json.loads(json.dumps(want_user)):
        errs.append("user segments differ from spec.py")

    for i, seg in enumerate(sys_segs + user_segs):
        if seg["kind"] != "ba1":
            continue
        derived = spec.ba1_segment_text(seg)
        if derived != seg["text"]:
            errs.append(f"segment {i} ({seg['source'].get('note', seg['source'])}) differs from 1.0 source")

    # cross-checks outside the spec
    task_text = src.level_task(cat, lvl)
    for old, new in spec.USER_EDITS.get((cat, lvl), []):
        task_text = task_text.replace(old, new)
    if task["user_message"] != task_text:
        errs.append("user_message != levels.yaml task with its edits")
    sp = task["system_prompt"]
    for word in spec.FORBIDDEN_IN_PROMPTS:
        if word in sp or word in task["user_message"]:
            errs.append(f"prompt mentions {word!r}, which the model cannot see")
    body = sp.split("<available_blocks>\n", 1)[1].split("\n</available_blocks>", 1)[0] if "<available_blocks>\n" in sp else None
    if body != blocks_out:
        errs.append("<available_blocks> body != 1.0 Blocks()() output")
    for name, cfg in configs.items():
        if cfg.get("disable") or name == "Starting Block":
            continue
        head = f"<{name}> mass: {cfg['weight']}" if cfg["type"] == "connection" else f"<{name}> shape: {cfg['shape']}, mass: {cfg['weight']}"
        if head not in sp:
            errs.append(f"missing block header {head!r}")
        if f"Description: {cfg['description']}" not in sp:
            errs.append(f"missing 1.0 description of {name}")

    if path.read_bytes() != gen_tasks.render_file(gen_tasks.build_task(cat, lvl, bset)):
        errs.append("file differs from a fresh generation")
    return errs


def main() -> int:
    blocks_out = src.blocks_call_output()
    configs = src.block_configs()
    expected = {f"{b}/{src.cell_name(c, l)}.json" for b in spec.BLOCKSETS for c, l in src.CELLS}
    present = {str(p.relative_to(HERE)) for p in HERE.glob("*/*.json")}
    failed = 0
    for rel in sorted(expected - present):
        print(f"FAIL {rel}: missing")
        failed += 1
    for rel in sorted(present - expected):
        print(f"FAIL {rel}: not a known cell")
        failed += 1
    for rel in sorted(expected & present):
        errs = check_file(HERE / rel, blocks_out, configs)
        task = json.loads((HERE / rel).read_text(encoding="utf-8"))
        n_ba1 = sum(len(s["text"]) for s in task["system_segments"] + task["user_segments"] if s["kind"] == "ba1")
        n_edits = sum(len(s["edits"]) for s in task["system_segments"] + task["user_segments"] if s["kind"] == "ba1")
        if errs:
            failed += 1
            print(f"FAIL {rel}")
            for e in errs:
                print(f"     {e}")
        else:
            print(f"ok   {rel}: system {len(task['system_prompt'])} chars, user {len(task['user_message'])} chars, "
                  f"{n_ba1} chars copied from 1.0 with {n_edits} listed edits")
    print(f"{len(expected) - failed}/{len(expected)} task files pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
