"""Paths and machine builders shared by the buildarena1 tests.

The tests that rebuild machines need this checkout set up and a 1.0 clone
(buildarena1/README.md section 7); without them they are skipped:
    BA1_ROOT             clone of BuildArena 1.0 (commit c3005b9 checked out)
    COLLIDER_DUMP_PATH   collider_dump.toml written by scripts/setup.py from the game
    BESIEGE_DATA_PATH    Besiege_Data, or a stand-in directory holding an empty Skins/
The last two are read from the process environment, else from this checkout's
.env, the way buildarena/paths.py reads them.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from buildarena1.evalkit.config import Paths
from buildarena1.evalkit.rebuild import use_v2


REPO_ROOT = Path(__file__).resolve().parents[2]
# resolved now, from the working directory the tests start in: the 1.0 harness
# (v1_harness.enter) changes the working directory later
_RAW_BA1_ROOT = os.environ.get("BA1_ROOT", "").strip()
BA1_ROOT = str(Path(_RAW_BA1_ROOT).expanduser().resolve()) if _RAW_BA1_ROOT else None


def _setting(key: str) -> str | None:
    """``key`` from the environment, else from this checkout's .env (relative paths
    resolve from the repository root, as in buildarena/paths.py)."""
    value = os.environ.get(key, "").strip()
    env_file = REPO_ROOT / ".env"
    if not value and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, sep, rest = line.strip().partition("=")
            if sep and name.strip() == key:
                value = rest.strip().strip('"').strip("'")
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else REPO_ROOT / path)


def paths() -> Paths:
    """The four paths the rebuild needs; raises unittest.SkipTest naming what is missing.
    A BA1_ROOT that is set but is not a 1.0 checkout raises RuntimeError instead."""
    if BA1_ROOT and not Path(BA1_ROOT, "analyze", "sim_common.py").is_file():
        raise RuntimeError(f"BA1_ROOT must point at a BuildArena 1.0 checkout (got {_RAW_BA1_ROOT!r}, "
                           f"resolved to {BA1_ROOT!r}).")
    found = {"BA1_ROOT": BA1_ROOT,
             "COLLIDER_DUMP_PATH": _setting("COLLIDER_DUMP_PATH"),
             "BESIEGE_DATA_PATH": _setting("BESIEGE_DATA_PATH")}
    missing = [k for k, v in found.items() if not v or not Path(v).exists()]
    if missing:
        raise unittest.SkipTest(f"needs {', '.join(missing)} (buildarena1/README.md section 7)")
    return Paths(v1_root=Path(found["BA1_ROOT"]), v2_root=REPO_ROOT,
                 collider_dump=Path(found["COLLIDER_DUMP_PATH"]),
                 besiege_data=Path(found["BESIEGE_DATA_PATH"]))


def v2_ready() -> Paths:
    p = paths()
    use_v2(p)
    return p


def scratch(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def build_history(ops: list[tuple[str, dict]], out: Path, name: str = "synthetic") -> Path:
    """Run ops on a fresh 2.0 Machine and save its valid history like the MCP does."""
    v2_ready()
    from buildarena.build import Machine
    machine = Machine(name=name, save_dir=str(out), write_full_history=False)
    for op, params in ops:
        getattr(machine, op)(**params)
    if len(machine.operation_history) != len(ops):
        raise AssertionError(f"Synthetic build kept {len(machine.operation_history)} of {len(ops)} ops.")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.json"
    path.write_text(json.dumps(machine.operation_history, indent=2), encoding="utf-8")
    path.with_suffix(".bsg").write_text(machine.to_xml(), encoding="utf-8")   # what the MCP saves beside it
    return path


def mcp_session(calls: list[tuple[str, dict]], machine_dir: Path, name: str) -> Path:
    """Run tool calls the way the MCP server does (FastMCP's Tool.run, an
    exception becomes the error text "Error executing tool <name>: ..."), and
    leave what a build session keeps: machine/<name>.json (valid history),
    machine/<name>.bsg, and tool_calls.jsonl (README section 3) one level
    up. Unlike build_history, calls may fail or raise. Returns the history path."""
    import asyncio
    v2_ready()
    from buildarena.build import Machine
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.server.fastmcp.tools.base import Tool

    machine = Machine(name=name, save_dir=str(machine_dir), write_full_history=False)
    rows = []
    for seq, (tool, arguments) in enumerate([("create_machine_lifespan", {"machine_name": name})] + calls, 1):
        if tool == "create_machine_lifespan":
            text, error = f"Created machine lifespan '{name}'", False
        else:
            try:
                text, error = asyncio.run(Tool.from_function(getattr(machine, tool), name=tool).run(arguments)), False
            except ToolError as exc:
                text, error = str(exc), True
        rows.append({"seq": seq, "id": f"call_{seq}", "name": f"mcp__buildarena__{tool}", "input": arguments,
                     "message_id": f"m{seq}", "is_error": error,
                     "result": text if error else json.dumps({"result": text})})
    machine_dir.mkdir(parents=True, exist_ok=True)
    path = machine_dir / f"{name}.json"
    path.write_text(json.dumps(machine.operation_history, indent=2), encoding="utf-8")
    path.with_suffix(".bsg").write_text(machine.to_xml(), encoding="utf-8")
    (machine_dir.parent / "tool_calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def attach(base, face, block):
    return ("attach_block_to", {"base_block": base, "face": face, "new_block": block})


# Starting Block faces: f0 east, f1 west, f2 up, f3 down, f4 north, f5 south.
# Single Wooden Block attached east: f0 south, f1 north, f2 up, f3 down, f4 east.
CAR = [("start", {}), attach(1, "f0", "Single Wooden Block"), attach(1, "f1", "Single Wooden Block"),
       attach(2, "f0", "Wheel"), attach(2, "f1", "Wheel"), attach(3, "f0", "Wheel"), attach(3, "f1", "Wheel")]
ROCKET = [("start", {}), attach(1, "f0", "Single Wooden Block"), attach(1, "f2", "Water Cannon"),
          attach(1, "f4", "Torch")]
BRIDGE_EAST = [("start", {}), attach(1, "f0", "Single Wooden Block"), attach(2, "f4", "Single Wooden Block"),
               attach(3, "f4", "Single Wooden Block")]
