"""Assemble ``block_channel_catalog.json`` from Inspector artefacts.

Current ToolKit Inspector writes collider dump, behaviour types, and a
live keylist cache. It does not emit the catalog file. Channel names and
slider keys come from authored control descriptors; KeyList positions and
native keys come from the Inspector dump. Extra authored names without a
KeyList slot are not invented as addresses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from buildarena.control_descriptor_loader import load_control_semantics

from .inspector import CHANNEL_CATALOG_NAME, InspectorError


def assemble_channel_catalog(*, behaviour_path: Path, dest: Path) -> dict[str, Any]:
    if not behaviour_path.is_file():
        raise InspectorError(
            f"Cannot assemble {CHANNEL_CATALOG_NAME}: behaviour types file is missing at {behaviour_path}."
        )
    payload = json.loads(behaviour_path.read_text(encoding="utf-8-sig"))
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise InspectorError(f"{behaviour_path} has no blocks array.")

    blocks: list[dict[str, Any]] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict) or "block_id" not in raw:
            raise InspectorError(f"{behaviour_path} contains a block entry without block_id.")
        block_id = int(raw["block_id"])
        native_keys = [str(item) for item in raw.get("key_list_channels") or [] if str(item)]
        semantics = load_control_semantics(block_id=block_id)
        authored_names = list(semantics.descriptions) if semantics is not None else []
        ignored = set(semantics.ignored) if semantics is not None else set()
        channels: list[dict[str, Any]] = []
        for index, native_key in enumerate(native_keys):
            if f"channel_{index}" in ignored:
                continue
            semantic_name = authored_names[index] if index < len(authored_names) else None
            channels.append(
                {
                    "channel_index": index,
                    "semantic_name": semantic_name,
                    "semantic_member_kind": None,
                    "native_keys": native_key,
                }
            )
        sliders: list[dict[str, Any]] = []
        if semantics is not None:
            for slider_name in semantics.sliders:
                sliders.append(
                    {
                        "name": slider_name,
                        "minimum": None,
                        "maximum": None,
                        "default": None,
                    }
                )
        blocks.append(
            {
                "block_id": block_id,
                "block_name": str(raw.get("block_name", "")),
                "behaviour_type_full_name": str(raw.get("behaviour_type_full_name", "")),
                "channels": channels,
                "sliders": sliders,
            }
        )

    catalog = {
        "schema": "buildarena.block_channel_catalog.v1",
        "source": "assembled_from_inspector",
        "blocks": blocks,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog
