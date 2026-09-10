"""Run-bound semantic names joined to authoritative game-published addresses.

The CLI owns the authored block/type metadata. This SDK module never infers
addresses from a BSG or a key name, and never changes a runtime channel index.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

BINDINGS_SCHEMA = "buildarena.control_bindings.v1"
BINDINGS_VERSION = 1
BINDINGS_FILE = "control_bindings.json"
ENV_CONTROL_BINDINGS = "BUILDARENA_CONTROL_BINDINGS"
MANUAL_CAMERA_BLOCK_ID = 58


def _index(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    return value


def bind_channels(channels: list, *, path: Path, run_id: str) -> list:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("schema") != BINDINGS_SCHEMA or payload.get("keylist_binding_version") != BINDINGS_VERSION:
        raise ValueError(f"Unsupported control bindings in {path}; regenerate with this CLI.")
    if not run_id or payload.get("run_id") != run_id:
        raise ValueError("Control bindings run_id does not match the runtime block table.")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("Control bindings require a blocks array.")
    by_guid = {}
    locals_seen = set()
    expected = {}
    for block in blocks:
        guid = block.get("guid")
        if not isinstance(guid, str) or not guid:
            raise ValueError("Control binding block requires a GUID.")
        guid = guid.lower()
        local = _index(block.get("local_index"), "binding local_index")
        block_id = _index(block.get("block_id"), "binding block_id")
        manual_camera = block_id == MANUAL_CAMERA_BLOCK_ID
        if guid in by_guid or local in locals_seen:
            raise ValueError("Duplicate block identity in control bindings.")
        slots = block.get("channels")
        ignored = block.get("ignored_keylist_indices", [])
        if not isinstance(slots, list) or not isinstance(ignored, list):
            raise ValueError("Control binding channels/ignored indices must be arrays.")
        if manual_camera and slots:
            raise ValueError("Camera Blocks are manually controlled; regenerate bindings without camera channels.")
        ignored = {_index(i, "ignored KeyList index") for i in ignored}
        names_seen = set()
        for slot in slots:
            index = _index(slot.get("keylist_index"), "binding KeyList index")
            name, aliases = slot.get("name"), slot.get("aliases", [])
            if not isinstance(name, str) or not name or not isinstance(aliases, list):
                raise ValueError("Control binding requires a name and aliases array.")
            names = (name, *aliases)
            if any(not isinstance(n, str) or not n for n in names):
                raise ValueError("Control binding names must be non-empty strings.")
            if len(set(names)) != len(names) or names_seen.intersection(names):
                raise ValueError(f"Ambiguous control binding names on block {local}.")
            if (guid, index) in expected or index in ignored:
                raise ValueError(f"Duplicate/ignored KeyList slot on block {local}.")
            expected[(guid, index)] = (local, names)
            names_seen.update(names)
        by_guid[guid] = (local, ignored, manual_camera)
        locals_seen.add(local)
    result, found = [], set()
    for channel in channels:
        guid = channel.block_guid.lower()
        identity = by_guid.get(guid)
        if identity is None or identity[0] != channel.local_index:
            raise ValueError(f"Runtime channel {channel.index} has a stale/unknown block identity.")
        key = (guid, channel.keylist_index)
        if key in found:
            raise ValueError(f"Duplicate runtime KeyList address {key}.")
        found.add(key)
        # Cameras retain their native game keys, but expose no SDK controls.
        # Match authored type and runtime GUID/local index; never ignore an
        # unknown address merely because it is absent from the catalog.
        if identity[2] or channel.keylist_index in identity[1]:
            continue
        binding = expected.get(key)
        if binding is None:
            raise ValueError(f"Unmapped runtime KeyList address {key}; re-probe the catalog.")
        _, names = binding
        positional = (f"keylist_{channel.keylist_index}", f"channel_{channel.keylist_index}")
        if channel.name not in (*names, *positional):
            raise ValueError(
                f"Runtime name {channel.name!r} conflicts with {names[0]!r} at {key}; "
                "verify the explicit alias/slot mapping."
            )
        result.append(replace(channel, name=names[0], aliases=tuple(dict.fromkeys(
            (*names[1:], *positional)
        ))))
    missing = set(expected) - found
    if missing:
        raise ValueError(f"Runtime block table is missing bound KeyList addresses: {sorted(missing)}.")
    return result
