"""Support-cell banks made of two scaled, very heavy Ballast blocks.

1.0 ran the support cells in the level editor on asset/support_{soft,medium,hard}.blv:
two level-editor terrain objects (Prefab 7000, physics off) whose Position and
Scale the file gives. 2.0 cannot load those files (README section 5), so in a
plain sandbox each terrain
object is stood in for by one Ballast block, scaled to the same box and given a
very large mass so the bridge and cargo cannot move it. The banks are written
into the evaluation BSG next to the machine; they are not in the block table
the judge reads, and result.json says the terrain came from here.

Geometry. The .blv objects are read from the 1.0 tree. A Prefab 7000 object is
taken to stand on its Position, its top at Position.y + Scale.y (only then do
1.0's bridge height 6 and cargo threshold 4 make sense); for the
1.0 files that is a top at y = 5. A Ballast's collider is a 0.95 x 0.95 x 1 box
with its origin on one face, extending along local +z (collider_dump.toml block
35, the same on 1.90 and 1.91). With 1.0's cargo rotation local x stays world x,
local y runs along world z and local z points up (measured in the game),
so scale (Sx / 0.95, Sz / 0.95, Sy) at the object's
Position gives the object's box.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import legacy as L

BALLAST_COLLIDER = (0.95, 0.95, 1.0)
TERRAIN_PREFAB = "7000"
SOURCE = "ballast_banks"


def blv_objects(blv: Path) -> list[dict]:
    """Prefab 7000 objects of a 1.0 level file: position and scale."""
    root = ET.fromstring(Path(blv).read_bytes().decode("utf-8-sig"))
    out = []
    for obj in root.iter("Object"):
        if obj.get("Prefab") != TERRAIN_PREFAB:
            continue
        pos = tuple(float(obj.find("Position").get(k)) for k in "xyz")
        scale = tuple(float(obj.find("Scale").get(k)) for k in "xyz")
        out.append({"id": obj.get("ID"), "position": pos, "scale": scale})
    if len(out) != 2:
        raise ValueError(f"{blv}: expected two Prefab {TERRAIN_PREFAB} objects, found {len(out)}.")
    return out


# --- quaternions as (x, y, z, w) ----------------------------------------------
def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def _qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def _qrot(q, v):
    x, y, z, w = _qmul(_qmul(q, (v[0], v[1], v[2], 0.0)), _qconj(q))
    return (x, y, z)


def bank_blocks(objects: list[dict], *, global_position, global_rotation, mass: float,
                lift: float, guid_seed: str) -> list[dict]:
    """World boxes of the .blv objects, as Ballast blocks in the machine's local frame."""
    inv = _qconj(tuple(global_rotation))
    rows = []
    for i, obj in enumerate(objects, start=1):
        px, py, pz = obj["position"]
        sx, sy, sz = obj["scale"]
        world = (px, py + lift, pz)
        rel = tuple(world[k] - global_position[k] for k in range(3))
        rows.append({
            "name": f"bank_{i}", "blv_object_id": obj["id"],
            "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"buildarena:{guid_seed}:terrain:{i}")),
            "world_box": {"x": [px - sx / 2, px + sx / 2], "y": [py + lift, py + lift + sy],
                          "z": [pz - sz / 2, pz + sz / 2]},
            "local_position": _qrot(inv, rel),
            "local_rotation": _qmul(inv, L.BALLAST_ROTATION_XYZW),
            "scale": (sx / BALLAST_COLLIDER[0], sz / BALLAST_COLLIDER[1], sy / BALLAST_COLLIDER[2]),
            "mass": mass,
        })
    return rows


def _element(row: dict) -> ET.Element:
    block = ET.Element("Block", {"id": L.BALLAST_BLOCK_TYPE_ID, "guid": row["guid"]})
    t = ET.SubElement(block, "Transform")
    for tag, keys, vals in (("Position", "xyz", row["local_position"]), ("Rotation", "xyzw", row["local_rotation"]),
                            ("Scale", "xyz", row["scale"])):
        ET.SubElement(t, tag, {k: repr(float(v)) for k, v in zip(keys, vals)})
    data = ET.SubElement(block, "Data")
    ET.SubElement(data, "Single", {"key": "bmt-mass"}).text = repr(float(row["mass"]))
    return block


def add_support_banks(bsg: Path, blv: Path, *, mass: float, lift: float = 0.0) -> dict:
    """Append the two banks to an exported support BSG in place; return what was written."""
    tree = ET.parse(bsg)
    root = tree.getroot()
    g = root.find("Global")
    gpos = tuple(float(g.find("Position").get(k)) for k in "xyz")
    grot = tuple(float(g.find("Rotation").get(k)) for k in "xyzw")
    seed = hashlib.sha256(bsg.read_bytes()).hexdigest()
    rows = bank_blocks(blv_objects(blv), global_position=gpos, global_rotation=grot, mass=mass, lift=lift,
                       guid_seed=seed)
    blocks = root.find("Blocks")
    for row in rows:
        blocks.append(_element(row))
    ET.indent(tree, space="    ")
    tree.write(bsg, encoding="utf-8", xml_declaration=True)
    return {"source": SOURCE, "level_file": str(blv), "level_file_sha256": hashlib.sha256(Path(blv).read_bytes()).hexdigest(),
            "mass": mass, "lift": lift, "banks": rows,
            "note": "Banks are two scaled Ballast blocks in a sandbox, not the level file's terrain objects."}


def apply_to_prepared(prepared_dir: Path, v1_root: Path, level: str, *, mass: float, lift: float = 0.0) -> dict:
    """add_support_banks on prepared/<name>_eval.bsg and record it in prepared/block_map.json."""
    manifest_path = prepared_dir / "block_map.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bsg = Path(manifest["bsg"])
    terrain = add_support_banks(bsg, Path(v1_root) / "asset" / f"support_{level}.blv", mass=mass, lift=lift)
    manifest["terrain"] = terrain
    manifest["bsg_sha256"] = hashlib.sha256(bsg.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
