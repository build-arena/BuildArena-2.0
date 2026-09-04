from __future__ import annotations

from pathlib import Path
import tomllib

from .paths import get_block_authoring_path


def load_block_authoring(
    *,
    authoring_path: Path | None = None,
) -> dict[int, dict[str, str]]:
    resolved_path = (
        authoring_path if authoring_path is not None else get_block_authoring_path()
    )
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Block authoring file not found: {resolved_path}")

    with open(resolved_path, "rb") as file:
        authoring_obj = tomllib.load(file)

    by_id_raw = authoring_obj.get("by_id", {})
    if not isinstance(by_id_raw, dict):
        raise ValueError("Block authoring file must define a [by_id] table.")

    result: dict[int, dict[str, str]] = {}
    names: dict[str, int] = {}
    for raw_id, raw_entry in by_id_raw.items():
        block_id = int(raw_id)
        if not isinstance(raw_entry, dict):
            raise ValueError(f"Block authoring entry for id={block_id} must be a table.")
        block_name = str(raw_entry.get("block_name", "")).strip()
        if block_name == "":
            raise ValueError(
                f"{resolved_path} [by_id.{block_id}] requires a unique block_name."
            )
        previous = names.get(block_name)
        if previous is not None:
            raise ValueError(
                f"{resolved_path} uses block_name {block_name!r} for both id "
                f"{previous} and id {block_id}. The authored name is globally unique; "
                "aliases are not allowed."
            )
        names[block_name] = block_id
        result[block_id] = {
            "block_name": block_name,
            "summary": str(raw_entry.get("summary", "")).strip(),
            "description": str(raw_entry.get("description", "")).strip(),
            "descriptor": str(raw_entry.get("descriptor", "")).strip(),
            "control_descriptor": str(raw_entry.get("control_descriptor", "")).strip(),
        }
    return result


def build_descriptor_map_from_authoring(
    *,
    authoring: dict[int, dict[str, str]],
) -> dict[int, str]:
    descriptor_map: dict[int, str] = {}
    for block_id, entry in authoring.items():
        descriptor_name = str(entry.get("descriptor", "")).strip()
        if descriptor_name == "":
            continue
        descriptor_map[block_id] = descriptor_name
    return descriptor_map


def build_control_descriptor_map_from_authoring(
    *,
    authoring: dict[int, dict[str, str]],
) -> dict[int, str]:
    """Map block ids to modules under ``blocks.control_descriptors``."""
    descriptor_map: dict[int, str] = {}
    for block_id, entry in authoring.items():
        descriptor_name = str(entry.get("control_descriptor", "")).strip()
        if descriptor_name:
            descriptor_map[block_id] = descriptor_name
    return descriptor_map
