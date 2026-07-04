from __future__ import annotations

from pathlib import Path
import tomllib

from .paths import get_block_authoring_path

DEFAULT_BLOCK_AUTHORING_PATH = get_block_authoring_path()


def load_block_authoring(
    *,
    authoring_path: Path = DEFAULT_BLOCK_AUTHORING_PATH,
) -> dict[int, dict[str, str]]:
    if not authoring_path.is_file():
        return {}

    with open(authoring_path, "rb") as file:
        authoring_obj = tomllib.load(file)

    by_id_raw = authoring_obj.get("by_id", {})
    if not isinstance(by_id_raw, dict):
        raise ValueError("Block authoring file must define a [by_id] table.")

    result: dict[int, dict[str, str]] = {}
    for raw_id, raw_entry in by_id_raw.items():
        block_id = int(raw_id)
        if not isinstance(raw_entry, dict):
            raise ValueError(f"Block authoring entry for id={block_id} must be a table.")
        result[block_id] = {
            "block_name": str(raw_entry.get("block_name", "")).strip(),
            "summary": str(raw_entry.get("summary", "")).strip(),
            "description": str(raw_entry.get("description", "")).strip(),
            "descriptor": str(raw_entry.get("descriptor", "")).strip(),
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
