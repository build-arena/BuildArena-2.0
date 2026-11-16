"""Cargo mass written into the evaluation BSG (``cargo_mass_mode = "fixed_bsg"``,
a standby for a game without the cargo-mass plugin).

Build 24131898 publishes the Ballast mass slider as 0.2..3 and does not take
larger values sent at run time, but it does take a larger mass written into the
BSG. For transport Lv.2 and Lv.3 the
task text describes a 50-unit cargo, so with ``EvalConfig.transport_cargo_mass_in_bsg``
set the cargo starts at that mass and the controller sends no mass at all
(1.0's 2-second ramp from 5 to 50 is dropped). The value is recorded in
block_map.json and in every controller_trace.jsonl line.

With a fixed mass the cargo is not dropped from 3 above
the highest block centre as in 1.0 (there it weighed 5 to 12.5 on landing);
its underside is put ``cargo_drop_above_top`` above the machine's highest
collider point. The cargo's origin is on its underside (collider_dump.toml
block 35), so the BSG position y is that point plus the gap.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import xml.etree.ElementTree as ET
from pathlib import Path

from . import legacy as L


def machine_top_y(machine) -> float:
    """Highest collider point of a rebuilt 2.0 machine in its own (virtual) frame,
    the same geometry Machine.compute_spawn_height uses for the lowest point."""
    top = None
    for block in machine.blocks.values():
        mesh = block.collider if block.collider is not None else block.outline
        if mesh is None or len(mesh.vertices) == 0:
            raise RuntimeError(f"Block {block.name!r} has no collider or outline geometry.")
        y = float(np.max(np.asarray(mesh.vertices, dtype=np.float64)[:, 1]))
        top = y if top is None else max(top, y)
    return top


def write_cargo_mass(prepared_dir: Path, mass: float, *, top_y: float | None = None,
                     drop: float | None = None) -> dict:
    """Set the cargo's bmt-mass in prepared/<name>_eval.bsg and, with ``drop``,
    put its underside ``drop`` above ``top_y``; update block_map.json."""
    manifest_path = Path(prepared_dir) / "block_map.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cargo = next(b for b in manifest["blocks"] if b["legacy_id"] == L.BALLAST_ID)
    bsg = Path(manifest["bsg"])
    tree = ET.parse(bsg)
    block = next(b for b in tree.getroot().find("Blocks") if b.get("guid") == cargo["guid"])
    entries = [n for n in block.find("Data") if n.get("key") == "bmt-mass"]
    if len(entries) != 1:
        raise ValueError(f"cargo block in {bsg} has {len(entries)} bmt-mass entries")
    entries[0].text = repr(float(mass))
    if drop is not None:
        position = block.find("Transform/Position")
        manifest["cargo_placement_1_0_y"] = float(position.get("y"))
        position.set("y", repr(float(top_y + drop)))
        manifest["cargo_drop_above_top"] = float(drop)
        manifest["machine_top_collider_y"] = float(top_y)
    ET.indent(tree, space="    ")
    tree.write(bsg, encoding="utf-8", xml_declaration=True)
    manifest["cargo_mass_in_bsg"] = float(mass)
    manifest["bsg_sha256"] = hashlib.sha256(bsg.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
