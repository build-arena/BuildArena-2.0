#!/usr/bin/env python3
"""Write buildarena1/tasks/<blockset>/<cell>.json, one build-session task per cell.

Each file holds the system prompt and the first user message of the build session, plus the
segments they were assembled from (spec.py) so check_tasks.py can re-derive every part
copied from BuildArena 1.0. Needs BA1_ROOT (see ba1_source.py). From the repository root:

    BA1_ROOT=<1.0 clone> uv run --with-requirements buildarena1/requirements.txt \
        python -m buildarena1.tasks.gen_tasks
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from buildarena1.tasks import ba1_source as src  # noqa: E402
from buildarena1.tasks import spec  # noqa: E402

HERE = Path(__file__).resolve().parent
SCHEMA = "buildarena1.build_task.v1"


def build_task(category: str, level: str, blockset: str) -> dict:
    sys_segs = spec.render(spec.system_segments(category, level, blockset))
    user_segs = spec.render(spec.user_segments(category, level))
    return {
        "schema": SCHEMA,
        "cell": src.cell_name(category, level),
        "category": category,
        "level": level,
        "level_label": spec.LEVEL_LABEL[level],
        "blockset": blockset,
        "status": "default",
        "machine_name": src.cell_name(category, level),
        "final_reply_sections": ["functionality", "motion_control"] if category == "transport" else [],
        "ba1_commit": src.BA1_COMMIT,
        "system_prompt": "".join(s["text"] for s in sys_segs),
        "user_message": "".join(s["text"] for s in user_segs),
        "system_segments": sys_segs,
        "user_segments": user_segs,
    }


def render_file(task: dict) -> bytes:
    return (json.dumps(task, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def all_tasks() -> dict[Path, bytes]:
    out = {}
    for blockset in spec.BLOCKSETS:
        for category, level in src.CELLS:
            task = build_task(category, level, blockset)
            out[HERE / blockset / f"{task['cell']}.json"] = render_file(task)
    return out


def main() -> int:
    files = all_tasks()
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    print(f"wrote {len(files)} task files under {HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
