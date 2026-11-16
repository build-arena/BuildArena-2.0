#!/usr/bin/env python3
"""Write registry/ba1_blocks/, the block set the build-session MCP server loads for the
BuildArena 1.0 tasks: this checkout's four files in blocks/, narrowed to the 1.0 blocks
(Starting Block, Single Wooden Block, Wheel, Water Cannon, Torch, and the connectors
Brace and Rope Winch). The MCP server reads it when started with

    BLOCK_REGISTRY_PATH=buildarena1/registry/ba1_blocks/block_registry.generated.toml
    BLOCK_AUTHORING_PATH=buildarena1/registry/ba1_blocks/block_authoring.toml
    BLOCK_ROLES_PATH=buildarena1/registry/ba1_blocks/block_roles.toml

(block_categories.toml is read from the registry's directory).

How ba1_blocks is narrowed (all other bytes are left as upstream wrote them):
  * block_registry.generated.toml: every [runtime_defs.<id>] section whose id is not in the
    set is deleted. Kept sections are untouched.
  * block_authoring.toml: every [by_id.<id>] section whose id is not in the set is deleted.
    Upstream's get_block_description reads this file without looking at the registry, so
    switching blocks off with `enabled = false` alone still lets the model read the
    description of any 2.0 block by name.
  * block_categories.toml: categories left without blocks get `enable = false`, which is
    upstream's own switch for an empty category (list_block_categories shows them as
    "Enabled: False"; list_blocks_by_category refuses them).
  * block_roles.toml: unchanged (it only maps ids to attach types).

usage (from the repository root):
    python -m buildarena1.registry.gen_registry            write the set
    python -m buildarena1.registry.gen_registry --check    exit 1 if the committed files differ
                                                           from a fresh build from blocks/
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
FILES = (
    "block_registry.generated.toml",
    "block_categories.toml",
    "block_authoring.toml",
    "block_roles.toml",
)
# 1.0 block -> 2.0 id and public name (buildarena1/README.md, section 2)
BA1_BLOCK_IDS = {
    0: "Starting Block",
    15: "Single Wooden Block",
    2: "Wheel",
    56: "Water Cannon",
    47: "Torch",
    7: "Brace",
    45: "Rope Winch",
}
BLOCKSETS = ("ba1_blocks",)


def _sections(text: str) -> list[str]:
    return re.split(r"(?m)^(?=\[)", text)


def _drop_sections(text: str, table: str, keep: set[int]) -> tuple[str, list[int]]:
    out, dropped = [], []
    for part in _sections(text):
        m = re.match(r"\[" + re.escape(table) + r"\.(\d+)\]", part)
        if m and int(m.group(1)) not in keep:
            dropped.append(int(m.group(1)))
            continue
        out.append(part)
    return "".join(out), dropped


def _kept_categories(registry_text: str) -> set[str]:
    cats: set[str] = set()
    for part in _sections(registry_text):
        if not part.startswith("[runtime_defs."):
            continue
        m = re.search(r"(?m)^category = \[(.*)\]$", part)
        if m is None:
            raise ValueError(f"runtime_defs section without category: {part[:60]!r}")
        cats.update(re.findall(r'"([^"]+)"', m.group(1)))
    return cats


def _narrow_categories(text: str, kept: set[str]) -> str:
    out = []
    for part in _sections(text):
        m = re.match(r"\[categories\.([^\]]+)\]", part)
        if m and m.group(1) not in kept:
            part, n = re.subn(r"(?m)^enable = true$", "enable = false", part)
            if n not in (0, 1):
                raise ValueError(f"unexpected enable lines in {m.group(1)}")
        out.append(part)
    return "".join(out)


def build() -> dict[str, dict[str, str]]:
    upstream = {name: (REPO_ROOT / "blocks" / name).read_bytes().decode("utf-8") for name in FILES}
    keep = set(BA1_BLOCK_IDS)
    reg, dropped_defs = _drop_sections(upstream["block_registry.generated.toml"], "runtime_defs", keep)
    auth, dropped_auth = _drop_sections(upstream["block_authoring.toml"], "by_id", keep)
    kept_def_ids = {int(x) for x in re.findall(r"(?m)^\[runtime_defs\.(\d+)\]", reg)}
    kept_auth_ids = {int(x) for x in re.findall(r"(?m)^\[by_id\.(\d+)\]", auth)}
    if kept_def_ids != keep or kept_auth_ids != keep:
        raise ValueError(f"narrowing kept {sorted(kept_def_ids)} / {sorted(kept_auth_ids)}, wanted {sorted(keep)}")
    for block_id, name in BA1_BLOCK_IDS.items():
        if f'[by_id.{block_id}]\nblock_name = "{name}"' not in auth:
            raise ValueError(f"block id {block_id} is not named {name!r} upstream")
    cats = _narrow_categories(upstream["block_categories.toml"], _kept_categories(reg))
    return {
        "ba1_blocks": {
            "block_registry.generated.toml": reg,
            "block_categories.toml": cats,
            "block_authoring.toml": auth,
            "block_roles.toml": upstream["block_roles.toml"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sets = build()
    bad = []
    for blockset, files in sets.items():
        for name, text in files.items():
            path = HERE / blockset / name
            if args.check:
                if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
                    bad.append(str(path.relative_to(HERE.parent)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(text.encode("utf-8"))
    if args.check:
        if bad:
            print("registry files differ from a fresh build:", *bad, sep="\n  ")
            return 1
        print(f"registry ok: {sum(len(f) for f in sets.values())} files match a fresh build from {REPO_ROOT / 'blocks'}")
    else:
        print("wrote", *(f"{b}/{n}" for b, f in sets.items() for n in f), sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
