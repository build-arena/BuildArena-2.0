"""Run the BuildArena 1.0 originals on a synthetic trajectory.

The 1.0 tree is imported read-only from ``$BA1_ROOT`` (a clone of
https://github.com/AI4Science-WestlakeU/BuildArena with commit c3005b9 checked
out; importing its judges also needs matplotlib, see
buildarena1/tests/requirements-v1.txt). Without it the callers skip, except
test_scoring_equivalence, which then compares with stored 1.0 results. A
BA1_ROOT that is set but is not a 1.0 checkout is an error, not a skip. A
relative BA1_ROOT is taken from the working directory at import time, since
enter() changes the working directory. Two
things the 1.0 code needs are provided here without touching it:

* 1.0 reads ``./blocks/configs`` relative to the working directory (spatial/
  build.py:24, 353) and its analysis writes ``datacache/`` relative to it
  (sim_common.py:211-215). Tests run inside a scratch directory holding a
  ``blocks`` symlink, so those reads work and the writes land in scratch.
* simulation/operations.py imports pyautogui/pyperclip/pynput at module level
  for its GUI automation. Stub modules stand in for those three so that its
  ``save_to_csv`` and ``clean_simulation_csv`` (pure file functions) import.

The 1.0 CSV is produced the way 1.0 produced it: Lua-format log lines written
here straight from spatial/build.py:1300-1316, then 1.0's own save_to_csv and
clean_simulation_csv. This code path shares nothing with evalkit.legacy_log.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_RAW_V1_ROOT = os.environ.get("BA1_ROOT", "").strip()
V1_ROOT = str(Path(_RAW_V1_ROOT).expanduser().resolve()) if _RAW_V1_ROOT else None
_STATE: dict = {}


def requested() -> bool:
    """BA1_ROOT is set: the 1.0 side must then run live (enter() fails if it cannot)."""
    return V1_ROOT is not None


def available() -> bool:
    return bool(V1_ROOT) and Path(V1_ROOT, "analyze", "sim_common.py").is_file()


def enter() -> dict:
    """Import the 1.0 modules once; returns them. Skips the caller if 1.0 is unavailable."""
    if _STATE:
        return _STATE
    if not V1_ROOT:
        raise unittest.SkipTest("needs BA1_ROOT, a BuildArena 1.0 clone (buildarena1/README.md section 7)")
    if not available():
        raise RuntimeError(f"BA1_ROOT must point at a BuildArena 1.0 checkout (got {_RAW_V1_ROOT!r}, resolved to {V1_ROOT!r}, which has no analyze/sim_common.py).")
    scratch = Path(tempfile.mkdtemp(prefix="v1_scratch_"))
    (scratch / "blocks").symlink_to(Path(V1_ROOT, "blocks"))
    _STATE["old_cwd"] = os.getcwd()
    os.chdir(scratch)
    sys.path.insert(0, V1_ROOT)
    for name in ("pyautogui", "pyperclip", "pynput", "pynput.mouse"):
        stub = types.ModuleType(name)
        if name == "pynput.mouse":
            stub.Controller = stub.Button = object
        sys.modules.setdefault(name, stub)
    import analyze.sim_transport as sim_transport
    import analyze.sim_support as sim_support
    import analyze.sim_lift as sim_lift
    import simulation.operations as operations
    import spatial.build as build
    import spatial.components as components
    _STATE.update(scratch=scratch, sim_transport=sim_transport, sim_support=sim_support,
                  sim_lift=sim_lift, operations=operations, build=build, components=components)
    return _STATE


def lua_log(ticks, tracked, *, quantize: bool, mass_max: float | None, ramp: float | None) -> str:
    """Log text of the 1.0 Lua script (spatial/build.py:1252-1261, 1285-1316).

    ``ticks`` is a list of (time, {legacy_id: (x, y, z, heated)}); ``tracked``
    is a list of (legacy_id, v1_name) in 1.0 print order.
    """
    fmt = (lambda v: "%.1f" % v) if quantize else (lambda v: repr(float(v)))
    lines = []
    for t, state in ticks:
        mass_value = None
        if mass_max is not None:
            frac = max(min((t - 0.5) / ramp, 1.0), 0.005)
            mass_value = 5.0 + (mass_max - 5.0) * frac
        for local_id, name in tracked:
            if local_id not in state:
                continue
            x, y, z, heated = state[local_id]
            if name == "Water Cannon":
                lines.append(f"{fmt(t)}, ID_{local_id}, {'true' if heated else 'false'}")
            if name == "Ballast":
                lines.append(f"{fmt(t)}, ID_{local_id}, {fmt(mass_value)}")
            lines.append(f"{fmt(t)}, ID_{local_id}, {fmt(x)}, {fmt(y)}, {fmt(z)}")
    return "\n".join(lines)


def run_v1(category: str, level: str, log_text: str, block_id_list: list[dict], workdir: Path) -> dict:
    """Write the 1.0 machine directory and call 1.0's analyze_simulation_<category>."""
    mods = enter()
    task_dir = workdir / f"{category}_{level}_model_20260101_0000_x"
    machine_id = "m0001"
    machine_dir = task_dir / "simulation" / machine_id
    machine_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{machine_id}_sim_1"
    (machine_dir / f"{stem}.bsg").write_text("", encoding="utf-8")
    (machine_dir / f"{stem}.json").write_text("[]", encoding="utf-8")
    (machine_dir / f"block_id_{stem}.json").write_text(json.dumps(block_id_list), encoding="utf-8")
    csv_path = machine_dir / f"simulation_log_{stem}.csv"
    with contextlib.redirect_stdout(io.StringIO()):   # clean_simulation_csv prints a line
        mods["operations"].save_to_csv(log_text, str(csv_path))
        mods["operations"].clean_simulation_csv(str(csv_path))
    analyze = {"transport": mods["sim_transport"].analyze_simulation_transport,
               "support": mods["sim_support"].analyze_simulation_support,
               "lift": mods["sim_lift"].analyze_simulation_lift}[category]
    return analyze(str(machine_dir), sim_round=1)
