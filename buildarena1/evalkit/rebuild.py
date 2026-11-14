"""Rebuild a sample's machine from its valid build history with 2.0, place it
the way 1.0 placed machines per category, add the cargo, back-fill 1.0 block
parameters, and export a BSG plus the Build ID / GUID / name table.

Placement follows 1.0 simulation/simulation_{transport,support,lift}.py; the
rebuild and GUID handling follow 2.0 control/run_example.py:78-114.

The machine evaluated is the one the MCP saved at the end of the build session
(machine/<name>.bsg). The valid history alone does not always replay into it:
two 2.0 tools can raise after they have changed the machine, and a call that
raises is in neither history file (build.py:1246-1248 removing a connector,
build.py:1413 rolling back a shift given as strings). So the export is compared
block by block with the MCP's BSG; on a difference the machine is rebuilt again
from the valid history with the calls that raised put back in call order
(``rebuild_session``), and if that export does not match either, ``prepare``
raises ``MachineMismatch`` and the sample gets no score.
buildarena1/README.md section 5 describes the two cases.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import sys
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from .config import EvalConfig, Paths
from . import legacy as L


class RebuildError(RuntimeError):
    """The history does not replay into the same machine."""


class MachineMismatch(RebuildError):
    """The machine prepared for evaluation is not the one the MCP saved."""

    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


def use_v2(paths: Paths) -> None:
    """Point this process at a 2.0 checkout (paths.py reads these variables)."""
    os.environ.update(paths.v2_environment())
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.dont_write_bytecode = True
    for entry in (str(paths.v2_root / "control"), str(paths.v2_root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)


@dataclass
class Rebuilt:
    machine: Any
    operations: list[dict]
    history_path: Path
    history_sha256: str
    replay_log: str
    starting_block_moved: bool = False   # see starting_block_first
    raised_calls: list[dict] | None = None   # rebuild_session only: the calls put back


def _open_history(history_path: Path, work_dir: Path, machine_name: str):
    from buildarena.build import Machine
    from buildarena.history_json import prepare_history_json

    work_dir.mkdir(parents=True, exist_ok=True)
    history_path = Path(history_path).resolve()
    machine = Machine(name=machine_name, save_dir=str(work_dir), write_full_history=False)
    resolved = prepare_history_json(history_json=history_path, machine=machine)
    raw = resolved.read_bytes()
    operations = json.loads(raw.decode("utf-8"))
    unknown = {op["op"] for op in operations} - machine.operations.keys()
    if unknown:
        raise RebuildError(f"Unsupported history operations: {sorted(unknown)}")
    return machine, operations, resolved, hashlib.sha256(raw).hexdigest()


def rebuild(history_path: Path, *, work_dir: Path, machine_name: str) -> Rebuilt:
    """Replay a valid-only history; the replayed op count must equal the file's
    (run_example.py:91-102)."""
    machine, operations, resolved, sha = _open_history(history_path, work_dir, machine_name)
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        machine.reset()   # what Machine.rebuild_from_history does, one operation at a time to name the one that raises
        for index, op in enumerate(operations):
            _replay_operation(machine, index, op)
    if len(machine.operation_history) != len(operations):
        raise RebuildError(f"Replay kept {len(machine.operation_history)} of {len(operations)} operations.")
    moved = starting_block_first(machine)
    return Rebuilt(machine, operations, resolved, sha, log.getvalue(), moved)


def _replay_operation(machine, index: int, op: dict) -> None:
    """Run one history operation; whatever 2.0 raises becomes a RebuildError that
    names the operation (its index in the history file) and chains the original."""
    try:
        machine.operations[op["op"]](**op["params"])
    except Exception as exc:
        raise RebuildError(f"History operation {index} ({op['op']}) raised {type(exc).__name__}: {exc}") from exc


# 2.0 operation groups whose tools change the machine's blocks (build.py @operation).
SESSION_REPLAY_GROUPS = ("build", "refine")


def _call_text(call: dict) -> str:
    """The text a tool call returned. Agent clients log it in one of two ways:
    FastMCP's structured {"result": ...} as a JSON string, or the text itself."""
    result = call.get("result")
    if not isinstance(result, str):
        return json.dumps(result)
    try:
        parsed = json.loads(result)
    except ValueError:
        return result
    return parsed["result"] if isinstance(parsed, dict) and isinstance(parsed.get("result"), str) else result


def _run_like_fastmcp(machine, tool: str, arguments: dict):
    """Validate the arguments the way the MCP server did before calling the tool
    (mcp FuncMetadata.call_fn_with_arg_validation, the version 2.0's uv.lock pins)."""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    fn = getattr(machine, tool)
    meta = func_metadata(fn)
    return fn(**meta.arg_model.model_validate(meta.pre_parse_json(arguments)).model_dump_one_level())


def _block_xml(machine) -> dict[str, str]:
    return {key: block.to_xml_node().render() for key, block in machine.blocks.items()}


def rebuild_session(history_path: Path, tool_calls: Path, *, work_dir: Path, machine_name: str) -> Rebuilt:
    """Replay the valid history with the tool calls that raised inside the MCP put
    back where they were made, so the machine ends as the MCP's did.

    The build session's calls are walked in order from after the last successful
    create_machine_lifespan or reset (both start an empty machine). A call to a
    build or refine tool that returned normally and returned what the next history
    entry records is that entry, and is replayed from the history. A call to such
    a tool that the MCP answered with "Error executing tool <name>: <error>" is
    run again with the arguments the model sent, and must raise the same error.
    Every other call is skipped: queries, calls the shell refused, and calls that
    returned an error message, which 2.0 makes without changing the machine
    beyond the order of ``machine.blocks`` and the last bits of a shift or twist
    it rolled back after a collision. Every history entry must be found.

    The walk stops at the last successful close_machine_lifespan, the save that
    wrote the MCP's BSG; calls after it (the lifespan is closed, they cannot
    change the machine) are not run. A session that never closed is walked to
    its end: whether its BSG came from a later save_machine or the server's
    shutdown save cannot be told from the calls, and the final comparison with
    the BSG decides.
    """
    machine, operations, resolved, sha = _open_history(history_path, work_dir, machine_name)
    calls = [json.loads(line) for line in Path(tool_calls).read_text(encoding="utf-8").splitlines() if line.strip()]
    tools = [call["name"].rsplit("__", 1)[-1] for call in calls]
    begin = 1 + max((i for i, (call, tool) in enumerate(zip(calls, tools))
                     if tool in ("create_machine_lifespan", "reset") and not call.get("is_error")), default=-1)
    # The last successful close_machine_lifespan is the save that wrote the MCP's BSG
    # (mcp_server.py close_machine_lifespan -> to_file) and it ends the lifespan, so
    # no later call can change the machine. Without one the BSG came from a
    # save_machine call or from the server's shutdown save (not told apart from
    # the calls), and every call after ``begin`` is considered.
    end = 1 + max((i for i, (call, tool) in enumerate(zip(calls, tools))
                   if i >= begin and tool == "close_machine_lifespan" and not call.get("is_error")), default=len(calls) - 1)
    replayed = {name for name, fn in machine.operations.items() if fn._group in SESSION_REPLAY_GROUPS}
    done, raised = 0, []
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        for call, tool in zip(calls[begin:end], tools[begin:end]):
            if tool not in replayed:
                continue
            text = _call_text(call)
            prefix = f"Error executing tool {tool}: "
            if call.get("is_error") and text.startswith(prefix):
                before = _block_xml(machine)
                try:
                    _run_like_fastmcp(machine, tool, call.get("input") or {})
                    again = None
                except Exception as exc:   # the MCP raised in this call; FastMCP caught it the same way
                    again = str(exc)
                if again != text[len(prefix):]:
                    raise RebuildError(f"Tool call {call.get('seq')} ({tool}) raised {text[len(prefix):]!r} in the "
                                       f"build session but {again!r} on replay.")
                after = _block_xml(machine)
                raised.append({"seq": call.get("seq"), "tool": tool, "input": call.get("input"),
                               "error": again, "removed": sorted(before.keys() - after.keys(), key=int),
                               "added": sorted(after.keys() - before.keys(), key=int),
                               "changed": sorted((k for k in before.keys() & after.keys() if before[k] != after[k]), key=int)})
            elif (not call.get("is_error") and done < len(operations) and operations[done]["op"] == tool
                  and operations[done]["result"] == text):
                _replay_operation(machine, done, operations[done])
                done += 1
    if done != len(operations):
        raise RebuildError(f"Only {done} of {len(operations)} history operations were found, in order, "
                           f"among the build session's tool calls.")
    if len(machine.operation_history) != len(operations):
        raise RebuildError(f"Session replay kept {len(machine.operation_history)} of {len(operations)} operations.")
    moved = starting_block_first(machine)
    return Rebuilt(machine, operations, resolved, sha, log.getvalue(), moved, raised)


def starting_block_first(machine) -> bool:
    """Put the Starting Block first in ``machine.blocks``, the others keeping their
    order; True if it had to be moved.

    2.0 ``Machine.refresh_block`` (build.py:1339-1352, used by ``shift_block`` and
    ``twist_block``) pops the block and re-inserts it at the end of
    ``machine.blocks``, so a history that shifts the Starting Block after other
    blocks exist replays with it elsewhere (twisting it raises in 2.0,
    ``Block.twist`` needs a rooted face, so no history twists it). That machine is legal:
    2.0 ``to_xml`` (build.py:1646-1652) and 1.0 ``to_xml`` (spatial/build.py:
    1339-1347) find the Starting Block by name and write it first, and so does
    the MCP's own BSG. The block table, ``judge.v1_block_data`` and
    ``judge.tracked_blocks`` take the table in that order (Starting Block, then
    the other blocks in ``machine.blocks`` order), so the order is put back here.
    A machine already in that order is left untouched.
    """
    starts = [key for key, block in machine.blocks.items() if block.name == "Starting Block"]
    if len(starts) != 1:
        raise RebuildError(f"The rebuilt machine has {len(starts)} Starting Blocks; expected 1.")
    key = starts[0]
    if next(iter(machine.blocks)) == key:
        return False
    start = machine.blocks.pop(key)
    others = list(machine.blocks.items())
    machine.blocks.clear()
    machine.blocks[key] = start
    machine.blocks.update(others)
    return True


@dataclass(frozen=True)
class Placement:
    shift_virtual: tuple[float, float, float]
    rotation_xyzw: tuple[float, float, float, float]
    spawn_y: float
    ballast_position: tuple[float, float, float] | None
    ballast_scale: tuple[float, float, float] | None
    rotated: bool
    stats: dict

    def global_position(self) -> tuple[float, float, float]:
        """Global Position the BSG gets (2.0 build.py:1622, 1.0 build.py:1235)."""
        return (self.shift_virtual[0], self.spawn_y - self.shift_virtual[1], self.shift_virtual[2])


def _geometry(machine) -> dict:
    """Centers of all blocks (connectors included), as 1.0 collects them."""
    xs, ys, zs = [], [], []
    for block in machine.blocks.values():
        pos = block.center_pos.virtual
        xs.append(float(pos[0])); ys.append(float(pos[1])); zs.append(float(pos[2]))
    return {"center_x": sum(xs) / len(xs), "center_z": sum(zs) / len(zs),
            "top_y": max(ys), "bottom_y": min(ys),
            "length_x": max(xs) - min(xs), "length_z": max(zs) - min(zs)}


def plan_placement(machine, category: str, level: str, cfg: EvalConfig) -> Placement:
    g = _geometry(machine)
    cx, cz, top = g["center_x"], g["center_z"], g["top_y"]
    identity = (0.0, 0.0, 0.0, 1.0)
    ballast_pos = (cx, top + L.BALLAST_DROP_ABOVE_TOP, cz)
    if category == "transport":   # simulation_transport.py:64-84
        scale = L.BALLAST_SCALE_TRANSPORT_HARD if level == "hard" else L.BALLAST_SCALE_DEFAULT
        has_cargo = level != "soft"
        spawn = machine.compute_spawn_height(clearance=cfg.spawn_clearance, rotation=list(identity))
        return Placement((-cx, 0.0, -cz), identity, spawn, ballast_pos if has_cargo else None,
                         scale if has_cargo else None, False, g)
    if category == "lift":        # simulation_lift.py:60 (no shift)
        spawn = machine.compute_spawn_height(clearance=cfg.spawn_clearance, rotation=list(identity))
        return Placement((0.0, 0.0, 0.0), identity, spawn, None, None, False, g)
    if category == "support":     # simulation_support.py:41-87
        gap = L.SUPPORT_GAP_LENGTH[level]
        rotate = g["length_z"] <= gap and g["length_x"] >= g["length_z"]
        shift = (cz, g["bottom_y"] - 1, cx) if rotate else (-cx, g["bottom_y"] - 1, -cz)
        rotation = L.SUPPORT_ROTATED_XYZW if rotate else identity
        return Placement(shift, rotation, L.V1_GLOBAL_Y, ballast_pos, L.BALLAST_SCALE_DEFAULT, rotate, g)
    raise ValueError(f"Unknown category {category!r}")


def block_table(machine, history_sha256: str) -> list[dict]:
    """Build ID -> stable GUID -> names, in machine (insertion) order."""
    rows = []
    for build_id, block in machine.blocks.items():
        rows.append({"build_id": str(build_id), "legacy_id": str(build_id),
                     "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"buildarena:{history_sha256}:{build_id}")),
                     "name_v2": block.name, "name_v1": L.V2_TO_V1_NAME.get(block.name),
                     "block_type_id": str(block.id), "old_guid": block.guid})
    return rows


def ballast_row(history_sha256: str) -> dict:
    return {"build_id": None, "legacy_id": L.BALLAST_ID,
            "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"buildarena:{history_sha256}:{L.BALLAST_ID}")),
            "name_v2": "Ballast", "name_v1": "Ballast", "block_type_id": L.BALLAST_BLOCK_TYPE_ID,
            "old_guid": None}


def _num(value: float) -> str:
    return repr(float(value))


def _ballast_element(row: dict, placement: Placement) -> ET.Element:
    """Cargo block as 1.0 wrote it (Block.to_xml, 1.0 spatial/build.py:254-296)."""
    block = ET.Element("Block", {"id": L.BALLAST_BLOCK_TYPE_ID, "guid": row["guid"]})
    transform = ET.SubElement(block, "Transform")
    px, py, pz = placement.ballast_position
    ET.SubElement(transform, "Position", {"x": _num(px), "y": _num(py), "z": _num(pz)})
    rx, ry, rz, rw = L.BALLAST_ROTATION_XYZW
    ET.SubElement(transform, "Rotation", {"x": _num(rx), "y": _num(ry), "z": _num(rz), "w": _num(rw)})
    sx, sy, sz = placement.ballast_scale
    ET.SubElement(transform, "Scale", {"x": _num(sx), "y": _num(sy), "z": _num(sz)})
    data = ET.SubElement(block, "Data")
    ET.SubElement(data, "Single", {"key": "bmt-mass"}).text = L.BALLAST_DATA_MASS
    return block


def backfill(tree: ET.Element, table: dict) -> list[dict]:
    """Write 1.0 block parameters into each matching block's Data."""
    written = []
    for block in tree.findall("Blocks/Block"):
        params = table.get(block.get("id"), ())
        if not params:
            continue
        data = block.find("Data")
        if data is None:
            data = ET.SubElement(block, "Data")
        for tag, key, text in params:
            for node in [n for n in data if n.get("key") == key]:
                data.remove(node)
            ET.SubElement(data, tag, {"key": key}).text = text
            written.append({"guid": block.get("guid"), "key": key, "value": text})
    return written


def export(rebuilt: Rebuilt, placement: Placement, cfg: EvalConfig, out_bsg: Path) -> dict:
    """Write the evaluation BSG; return the block table and what was written."""
    machine = rebuilt.machine
    table = block_table(machine, rebuilt.history_sha256)
    xml_text = machine.to_xml(shift_virtual=list(placement.shift_virtual),
                              rotation=list(placement.rotation_xyzw), spawn_y=placement.spawn_y)
    tree = ET.fromstring(xml_text)
    rebase = {row["old_guid"]: row["guid"] for row in table}
    for node in tree.iter():
        for key, value in list(node.attrib.items()):
            if value in rebase:
                node.set(key, rebase[value])
        if node.text and node.text.strip() in rebase:
            node.text = rebase[node.text.strip()]
    if placement.ballast_position is not None:
        cargo = ballast_row(rebuilt.history_sha256)
        tree.find("Blocks").append(_ballast_element(cargo, placement))
        table.append(cargo)
    written = backfill(tree, cfg.backfill_param_table) if cfg.backfill_params else []
    ET.indent(tree, space="    ")
    out_bsg.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(tree).write(out_bsg, encoding="utf-8", xml_declaration=True)
    for row in table:
        row.pop("old_guid")
    return {"blocks": table, "params_written": written,
            "bsg_sha256": hashlib.sha256(out_bsg.read_bytes()).hexdigest()}


# ------------------------------------------------- comparison with the MCP's BSG
MATCH_TOLERANCE = 1e-6   # exports of one machine on different hosts differ by about 1e-15


def _block_fields(block: ET.Element, skip_keys) -> dict:
    """Type, Transform and Data of a BSG block; Data keys in ``skip_keys`` (the
    1.0 parameters evalkit writes) are left out."""
    t = block.find("Transform")
    data = {}
    node = block.find("Data")
    for child in (node if node is not None else ()):
        key = child.get("key")
        if key in skip_keys.get(block.get("id"), ()):
            continue
        parts = [child.find(a) for a in "XYZ"]
        data[key] = [float(p.text) for p in parts] if all(p is not None for p in parts) else (child.text or "").strip()
    return {"type": block.get("id"),
            "position": [float(t.find("Position").get(k)) for k in "xyz"],
            "rotation": [float(t.find("Rotation").get(k)) for k in "xyzw"],
            "scale": [float(t.find("Scale").get(k)) for k in "xyz"], "data": data}


def _zyz(angles_deg) -> list[float]:
    """Rotation matrix of a connector's start/end-rotation, written by 2.0 as
    quaternion.as_euler_angles (z-y-z) in degrees plus 270 on the first angle
    (components.py:110-112). With the middle angle 0 or 180 only the sum or the
    difference of the other two is fixed, so the triples are compared as matrices."""
    def rz(t):
        return ((math.cos(t), -math.sin(t), 0.0), (math.sin(t), math.cos(t), 0.0), (0.0, 0.0, 1.0))

    def ry(t):
        return ((math.cos(t), 0.0, math.sin(t)), (0.0, 1.0, 0.0), (-math.sin(t), 0.0, math.cos(t)))

    def mul(a, b):
        return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))

    a, b, g = (math.radians(v) for v in angles_deg)
    return [v for row in mul(mul(rz(a), ry(b)), rz(g)) for v in row]


def _close(a, b) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= MATCH_TOLERANCE for x, y in zip(a, b))


def same_block(a: dict, b: dict) -> bool:
    """Same type, position, scale, rotation (q or -q), and Data (connector end
    points as vectors, their rotations as matrices, the rest as text)."""
    if a["type"] != b["type"] or not _close(a["position"], b["position"]) or not _close(a["scale"], b["scale"]):
        return False
    if not (_close(a["rotation"], b["rotation"]) or _close(a["rotation"], [-v for v in b["rotation"]])):
        return False
    if a["data"].keys() != b["data"].keys():
        return False
    for key, va in a["data"].items():
        vb = b["data"][key]
        if isinstance(va, list) != isinstance(vb, list):
            return False
        if isinstance(va, list) and not _close(*((_zyz(va), _zyz(vb)) if key.endswith("-rotation") else (va, vb))):
            return False
        if not isinstance(va, list) and va != vb:
            return False
    return True


def _skip_keys(cfg: EvalConfig) -> dict:
    return {t: {key for _, key, _ in params} for t, params in cfg.backfill_param_table.items()} if cfg.backfill_params else {}


def _pair_blocks(ours: list[dict], theirs: list[dict]) -> tuple[list[int], list[int]]:
    """One-to-one pairing of ``theirs`` with ``ours`` under ``same_block``; returns
    the indices left over on each side of a largest pairing. ``same_block`` is a
    tolerance test and not transitive, so a block can have several partners and a
    first-fit choice can strand a block that a different assignment would pair
    (augmenting paths, Kuhn's algorithm; a machine that pairs first-fit pairs the
    same way)."""
    partners = [[j for j, mine in enumerate(ours) if same_block(block, mine)] for block in theirs]
    owner: dict[int, int] = {}   # index in ours -> index in theirs
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * len(theirs) + 1000))

    def place(i: int, seen: set[int]) -> bool:
        for j in partners[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or place(owner[j], seen):
                owner[j] = i
                return True
        return False

    left_theirs = [i for i in range(len(theirs)) if not place(i, set())]
    return sorted(set(range(len(ours))) - set(owner)), left_theirs


def mcp_differences(eval_bsg: Path, blocks: list[dict], mcp_bsg: Path, cfg: EvalConfig,
                    extra_guids=()) -> dict | None:
    """Compare the machine blocks of the evaluation BSG with the MCP's BSG, one to
    one regardless of order. None when every block has a partner.

    The machine blocks are the block table's rows with a Build ID. Every other
    block of the evaluation BSG must be one the evaluation put there on purpose:
    the table's cargo row (no Build ID) and the GUIDs in ``extra_guids`` (the
    support banks). A block with any other GUID, a GUID the BSG has twice, or a
    table row without its block is a difference."""
    skip = _skip_keys(cfg)
    nodes = ET.parse(eval_bsg).getroot().findall("Blocks/Block")
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.get("guid")] = counts.get(node.get("guid"), 0) + 1
    by_guid = {node.get("guid"): node for node in nodes}
    machine_rows = [row for row in blocks if row["build_id"] is not None]
    registered = {row["guid"] for row in blocks} | set(extra_guids)
    duplicate_guids = sorted(guid for guid, n in counts.items() if n > 1)
    unregistered = [{"guid": node.get("guid"), "type": node.get("id")} for node in nodes if node.get("guid") not in registered]
    missing = [row["build_id"] for row in machine_rows if row["guid"] not in by_guid]
    row_guids = [row["guid"] for row in machine_rows]
    duplicate_rows = sorted({guid for guid in row_guids if row_guids.count(guid) > 1})
    ours = [(row["build_id"], _block_fields(by_guid[row["guid"]], skip)) for row in machine_rows if row["guid"] in by_guid]
    theirs = [_block_fields(el, skip) for el in ET.parse(mcp_bsg).getroot().findall("Blocks/Block")]
    left_ours, left_theirs = _pair_blocks([fields for _, fields in ours], theirs)
    only_mcp = [{"index": i, "type": theirs[i]["type"], "position": theirs[i]["position"]} for i in left_theirs]
    only_ours = [{"build_id": ours[j][0], "type": ours[j][1]["type"], "position": ours[j][1]["position"]} for j in left_ours]
    if not (missing or only_mcp or only_ours or unregistered or duplicate_guids or duplicate_rows):
        return None
    out = {"evaluation_blocks": len(ours) + len(missing), "mcp_blocks": len(theirs),
           "not_in_mcp": only_ours, "not_in_evaluation": only_mcp, "table_rows_without_block": missing}
    if unregistered:
        out["blocks_not_in_the_table"] = unregistered
    if duplicate_guids or duplicate_rows:
        out["duplicate_guids"] = sorted(set(duplicate_guids) | set(duplicate_rows))
    return out


@dataclass
class Prepared:
    rebuilt: Rebuilt
    placement: Placement
    manifest: dict
    machine_state: str                     # control-agent text, taken before export
    actions: dict[str, tuple[str, ...]]    # Build ID -> 1.0 actions of powered blocks
    block_ids: set[str]
    machine_check: dict = field(default_factory=lambda: {"status": "not_checked"})

    @property
    def blocks(self) -> list[dict]:
        return self.manifest["blocks"]

    @property
    def bsg(self) -> Path:
        return Path(self.manifest["bsg"])


def _place_and_export(rebuilt: Rebuilt, category: str, level: str, cfg: EvalConfig, bsg: Path):
    from .prompts import machine_state_from, powered_actions

    machine = rebuilt.machine
    state, actions = machine_state_from(machine), powered_actions(machine)
    block_ids = {str(k) for k in machine.blocks}
    placement = plan_placement(machine, category, level, cfg)
    return state, actions, block_ids, placement, export(rebuilt, placement, cfg, bsg)


def _correction(first: dict, session: Rebuilt) -> dict:
    """What block_map.json and result.json record about a corrected machine."""
    return {"rebuilt_by": "valid history plus the tool calls that raised in the MCP (rebuild.rebuild_session)",
            "valid_history_replay": first,
            "raised_calls_replayed": len(session.raised_calls),
            "raised_calls_that_changed_the_machine": [c for c in session.raised_calls
                                                      if c["removed"] or c["added"] or c["changed"]]}


def _rebuild_and_check(history_path: Path, category: str, level: str, cfg: EvalConfig, work: Path,
                       machine_name: str, bsg: Path, mcp_bsg: Path | None, tool_calls: Path | None):
    """Rebuild and export to ``bsg``, checked against ``mcp_bsg`` when given.
    Returns (the machine that matched, what ``_place_and_export`` returned, the correction record or None).
    A replay that cannot be made at all is a RebuildError; a machine that replays
    but is not the MCP's is a MachineMismatch."""
    try:
        rebuilt, first = rebuild(history_path, work_dir=work, machine_name=machine_name), None
    except RebuildError as exc:
        if mcp_bsg is None:
            raise
        rebuilt, first = None, {"error": str(exc)}
    if rebuilt is not None:
        out = _place_and_export(rebuilt, category, level, cfg, bsg)
        first = mcp_differences(bsg, out[4]["blocks"], mcp_bsg, cfg) if mcp_bsg is not None else None
    correction = None
    if first is not None:
        if tool_calls is None or not Path(tool_calls).is_file():
            if rebuilt is None:
                raise RebuildError(f"{first['error']} There is no tool_calls.jsonl to replay the session from.")
            raise MachineMismatch("The valid history does not rebuild the MCP's machine and there is no "
                                  "tool_calls.jsonl to replay the session from.", {"valid_history_replay": first})
        try:
            session = rebuild_session(history_path, tool_calls, work_dir=work, machine_name=machine_name)
        except RebuildError as exc:
            details = {"valid_history_replay": first, "session_replay": {"error": str(exc)}}
            if rebuilt is None:
                raise RebuildError(f"{first['error']} Session replay: {exc}") from exc
            raise MachineMismatch("Neither the valid history nor the session replay rebuilds the MCP's machine.",
                                  details) from exc
        out = _place_and_export(session, category, level, cfg, bsg)
        second = mcp_differences(bsg, out[4]["blocks"], mcp_bsg, cfg)
        if second is not None:
            raise MachineMismatch("Neither the valid history nor the session replay rebuilds the MCP's machine.",
                                  {"valid_history_replay": first, "session_replay": second})
        rebuilt, correction = session, _correction(first, session)
    return rebuilt, out, correction


def prepare(history_path: Path, category: str, level: str, cfg: EvalConfig, out_dir: Path,
            machine_name: str, *, mcp_bsg: Path | None = None, tool_calls: Path | None = None) -> Prepared:
    """Rebuild, place and export into ``out_dir``; writes block_map.json there.

    With ``mcp_bsg`` (the evaluation always passes it) the export must match
    that BSG block for block, by the valid history or else by ``rebuild_session``
    on ``tool_calls``; otherwise ``MachineMismatch`` (``RebuildError`` when no
    replay can be made at all). The BSG appears under its real name only after
    it matched. A machine the valid history already rebuilds right is exported
    exactly as without the check."""
    bsg = out_dir / f"{machine_name}_eval.bsg"
    candidate = out_dir / f"{machine_name}_eval.bsg.partial"   # takes the real name only once it matches the MCP's BSG
    try:
        rebuilt, out, correction = _rebuild_and_check(history_path, category, level, cfg, out_dir / "rebuild_tmp",
                                                      machine_name, candidate, mcp_bsg, tool_calls)
        os.replace(candidate, bsg)
    finally:
        candidate.unlink(missing_ok=True)
    state, actions, block_ids, placement, exported = out
    manifest = {"history": str(rebuilt.history_path), "history_sha256": rebuilt.history_sha256,
                "operations": len(rebuilt.operations), "replayed_operations": len(rebuilt.machine.operation_history),
                "bsg": str(bsg), "category": category, "level": level,
                "placement": {**asdict(placement), "global_position": placement.global_position()},
                **exported}
    if rebuilt.starting_block_moved:   # written only then, so other machines' block_map.json stay as they were
        manifest["starting_block_moved_first"] = True
    if correction is not None:         # likewise
        manifest["machine_corrected"] = correction
    (out_dir / "block_map.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "replay.log").write_text(rebuilt.replay_log, encoding="utf-8")
    (out_dir / "machine_state.txt").write_text(state, encoding="utf-8")
    check = {"status": "not_checked"}
    if mcp_bsg is not None:
        check = {"status": "corrected" if correction else "same", "mcp_bsg": str(mcp_bsg),
                 "mcp_bsg_sha256": hashlib.sha256(Path(mcp_bsg).read_bytes()).hexdigest(),
                 "machine_blocks": len(block_ids), **({"correction": correction} if correction else {})}
    return Prepared(rebuilt, placement, manifest, state, actions, block_ids, check)


def check_final(prep: Prepared, cfg: EvalConfig) -> None:
    """Compare the evaluation BSG once more after banks and cargo mass were
    written into it (sample.add_terrain, add_cargo_mass); raises MachineMismatch.
    Besides the machine blocks the BSG may hold the cargo row and the support
    banks (``manifest["terrain"]["banks"]``), nothing else. A BSG that fails is
    renamed to ``<name>_eval.bsg.unverified`` so no real-named file is left."""
    if prep.machine_check["status"] == "not_checked":
        return
    banks = [b["guid"] for b in (prep.manifest.get("terrain") or {}).get("banks", [])]
    diff = mcp_differences(prep.bsg, prep.blocks, Path(prep.machine_check["mcp_bsg"]), cfg, extra_guids=banks)
    if diff is not None:
        os.replace(prep.bsg, prep.bsg.with_name(prep.bsg.name + ".unverified"))
        raise MachineMismatch("The evaluation BSG no longer matches the MCP's machine after terrain and cargo.",
                              {"final_bsg": diff})
