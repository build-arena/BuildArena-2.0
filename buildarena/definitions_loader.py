from __future__ import annotations

from copy import deepcopy
from typing import Any
from pathlib import Path
import tomllib

from .mesh_loader import block_runtime_mesh_exists, load_registry
from .block_authoring import load_block_authoring
from .paths import get_block_registry_path, get_block_roles_path

# ── Defaults (immutable, overridable via function params) ─────────────

DEFAULT_SKIP_IDS: frozenset[int] = frozenset({12, 64, 1000})
"""IDs present in dump but not real blocks: 12=ScalingBlock (hidden/unused),
64=Magnet (scrapped), 1000=mod GUID."""

DEFAULT_SKIP_NAMES: frozenset[str] = frozenset({"Unused", "Unused3", "BuildNode", "BuildEdge", "BuildSurface"})

DEFAULT_NAME_ALIASES: dict[str, str] = {
    "Wooden Block":      "Double Wooden Block",
    "Small Wooden Block": "Single Wooden Block",
    "Powered Wheel":     "Wheel",
    "Winch":             "Rope Winch",
    "Flywheel":          "Fly Wheel",
}

DEFAULT_CONNECTION_NAMES: frozenset[str] = frozenset({"Brace", "Rope Winch", "Spring", "Rope Measure"})
DEFAULT_ROLES_PATH: Path = get_block_roles_path()
DEFAULT_REGISTRY_PATH: Path = get_block_registry_path()


def _camel_to_display(*, name: str) -> str:
    """Convert PascalCase to 'Display Name' without regex.

    Inserts a space before each uppercase letter that immediately follows
    a lowercase letter:  DoubleWoodenBlock → Double Wooden Block.
    """
    chars: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and name[i - 1].islower():
            chars.append(" ")
        chars.append(ch)
    return "".join(chars)


def build_block_maps(
    *,
    skip_ids: frozenset[int] = DEFAULT_SKIP_IDS,
    skip_names: frozenset[str] = DEFAULT_SKIP_NAMES,
    aliases: dict[str, str] | None = None,
    dump_path: Path | None = None,
) -> tuple[dict[str, int], dict[int, str]]:
    """Build BLOCK_ID_MAP and BLOCK_NAME_BY_ID from collider_dump.toml.

    All parameters have defaults so callers can override for testing or
    alternative dump files.
    """
    from .collider_loader import get_all_dump_blocks

    if aliases is None:
        aliases = dict(DEFAULT_NAME_ALIASES)

    dump_blocks = get_all_dump_blocks(dump_path=dump_path)

    id_map: dict[str, int] = {}
    name_by_id: dict[int, str] = {}

    for block_id, camel_name in sorted(dump_blocks.items()):
        if block_id in skip_ids:
            continue
        if camel_name in skip_names:
            continue

        display_name = _camel_to_display(name=camel_name)
        id_map[display_name] = block_id
        name_by_id[block_id] = display_name

    for alias, canonical in aliases.items():
        if canonical in id_map:
            id_map[alias] = id_map[canonical]

    return id_map, name_by_id


# Convenience module-level references (built once from defaults).
# Callers that need custom maps should call build_block_maps() directly.
BLOCK_ID_MAP, BLOCK_NAME_BY_ID = build_block_maps()


def _resolve_block_identity(
    *,
    raw_key: str,
    block_def: dict[str, Any],
    id_map: dict[str, int] | None = None,
    name_by_id: dict[int, str] | None = None,
) -> tuple[int | None, str]:
    """Resolve a registry entry to (block_id, canonical_display_name).

    Resolution order:
    1. Explicit ``id`` field in block_def  →  look up canonical name by ID
    2. raw_key is a digit string           →  use as ID, look up canonical name
    3. raw_key matches id_map              →  use mapped ID + canonical name

    Returns (None, raw_key) if the block cannot be identified — caller
    should skip that entry.  There is NO fallback / guessing.
    """
    if id_map is None:
        id_map = BLOCK_ID_MAP
    if name_by_id is None:
        name_by_id = BLOCK_NAME_BY_ID

    block_id: int | None = None

    if "id" in block_def and isinstance(block_def.get("id"), int):
        block_id = int(block_def["id"])
    elif isinstance(raw_key, str) and raw_key.isdigit():
        block_id = int(raw_key)
    else:
        mapped_id = id_map.get(raw_key)
        if mapped_id is not None:
            block_id = int(mapped_id)

    if block_id is None:
        return None, raw_key

    canonical_name = name_by_id.get(block_id)
    if canonical_name is None:
        return None, raw_key

    return block_id, canonical_name


def validate_registry_ids(
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    id_map: dict[str, int] | None = None,
) -> list[str]:
    """Cross-check runtime_defs id fields against the block ID map.

    Returns a list of warning strings (empty means all clear).
    """
    if id_map is None:
        id_map = BLOCK_ID_MAP

    warnings: list[str] = []
    blocks = load_runtime_blocks(registry_path=registry_path)
    for name, block in blocks.items():
        rid = block.get("id")
        if rid is None:
            warnings.append(f"[missing id] '{name}' has no id field")
            continue
        rid = int(rid)
        expected = id_map.get(name)
        if expected is None:
            warnings.append(f"[unknown name] '{name}' (id={rid}) not in id_map")
        elif expected != rid:
            warnings.append(
                f"[id mismatch] '{name}': registry id={rid}, expected id={expected}"
            )
    return warnings


def _normalize_optional_dict(*, value: Any) -> dict | bool:
    if value is False:
        return False
    if value in (None, ""):
        return False
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected dict/bool/empty for optional dict field, got {type(value)}")


def _normalize_faces(*, value: Any) -> dict | None:
    if value is False:
        return None
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    raise TypeError(f"Expected dict/bool/none for faces field, got {type(value)}")


def _normalize_spin_config(*, value: Any) -> dict | bool:
    if value is False:
        return False
    if value in (None, ""):
        return False
    if not isinstance(value, dict):
        raise TypeError(f"Expected dict/bool/empty for spin field, got {type(value)}")

    axis = value.get("axis")
    handedness = str(value.get("handedness", "")).strip().lower()
    if not isinstance(axis, list) or len(axis) != 3:
        raise ValueError(f"Spin config must provide axis=[x,y,z], got {axis}")
    if not all(isinstance(item, (int, float)) for item in axis):
        raise ValueError(f"Spin axis must be numeric, got {axis}")
    if handedness not in {"left", "right"}:
        raise ValueError(f"Spin handedness must be 'left' or 'right', got {handedness}")

    return {
        "axis": [float(axis[0]), float(axis[1]), float(axis[2])],
        "handedness": handedness,
    }


def _normalize_pointer_axis(*, value: Any, block_name: str) -> list[float] | bool:
    if value in (None, False, ""):
        return False
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"Pointer block '{block_name}' must define pointer_axis=[x,y,z], got {value}")
    if not all(isinstance(item, (int, float)) for item in value):
        raise ValueError(f"Pointer axis must be numeric for '{block_name}', got {value}")
    if sum(float(item) ** 2 for item in value) <= 1e-12:
        raise ValueError(f"Pointer axis cannot be zero for '{block_name}'")
    return [float(value[0]), float(value[1]), float(value[2])]


def _load_block_roles(*, roles_path: Path) -> dict[int, dict[str, Any]]:
    if not roles_path.is_file():
        raise FileNotFoundError(f"Block roles file not found: {roles_path}")
    with open(roles_path, "rb") as file:
        obj = tomllib.load(file)
    roles_raw = obj.get("roles", {})
    if not isinstance(roles_raw, dict):
        raise ValueError("Invalid block roles format: [roles] table missing")

    roles: dict[int, dict[str, Any]] = {}
    for key, value in roles_raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"Invalid role entry for id '{key}': expected table")
        block_id = int(key)
        block_name = str(value.get("block_name", "")).strip()
        if block_name == "":
            raise ValueError(f"Role entry for block id {block_id} requires block_name")
        role_type = str(value.get("type", "")).strip().lower()
        if role_type not in {"basic", "pointer", "connection"}:
            raise ValueError(f"Invalid role type for block id {block_id}: {role_type}")
        role: dict[str, Any] = {
            "type": role_type,
            "block_name": block_name,
        }
        if role_type == "pointer":
            if "pointer_axis" not in value:
                raise ValueError(f"Pointer role for block id {block_id} requires pointer_axis")
            role["pointer_axis"] = value["pointer_axis"]
        elif "pointer_axis" in value:
            raise ValueError(f"Only pointer role may define pointer_axis (block id {block_id})")
        roles[block_id] = role
    return roles


def load_runtime_blocks(
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    *,
    connection_names: frozenset[str] = DEFAULT_CONNECTION_NAMES,
    id_map: dict[str, int] | None = None,
    name_by_id: dict[int, str] | None = None,
    roles_path: Path = DEFAULT_ROLES_PATH,
) -> dict[str, dict[str, Any]]:
    if id_map is None:
        id_map = BLOCK_ID_MAP
    if name_by_id is None:
        name_by_id = BLOCK_NAME_BY_ID

    registry = load_registry(registry_path=registry_path)
    defaults = registry.get("runtime_defaults", {})
    defs = registry.get("runtime_defs", {})
    if not isinstance(defs, dict):
        raise ValueError("Expected [runtime_defs] in block_registry.generated.toml")
    roles = _load_block_roles(roles_path=roles_path)
    authoring = load_block_authoring()

    result: dict[str, dict[str, Any]] = {}
    for raw_key, raw_def in defs.items():
        if not isinstance(raw_def, dict):
            continue

        block_def = deepcopy(raw_def)
        if not isinstance(raw_key, str):
            raw_key = str(raw_key)

        block_id, block_name = _resolve_block_identity(
            raw_key=raw_key,
            block_def=block_def,
            id_map=id_map,
            name_by_id=name_by_id,
        )
        if block_id is None:
            continue

        block_def["id"] = int(block_id)
        block_def["name"] = block_name
        block_def["disable"] = not bool(block_def.get("enabled", True))
        block_def["enabled"] = bool(block_def.get("enabled", True))

        from .collider_loader import get_dump_block_name, infer_shape_root_from_dump, compute_root_offset
        dump_name = get_dump_block_name(block_id=block_id)
        inferred_shape_root = infer_shape_root_from_dump(block_id=block_id)

        if "shape" not in block_def and inferred_shape_root is not None:
            block_def["shape"] = inferred_shape_root["shape"]
        if "root" not in block_def and inferred_shape_root is not None:
            block_def["root"] = inferred_shape_root["root"]

        block_def.setdefault("shape", [1.0, 1.0, 1.0])
        block_def.setdefault("root", [0.0, 0.0, 0.0])
        block_def.setdefault("vec_base", [0.0, 0.0, 1.0])

        if "root_offset" not in block_def:
            block_def["root_offset"] = compute_root_offset(
                block_id=block_id,
                vec_base=block_def["vec_base"],
            )

        if "center_offset" not in block_def:
            vb = block_def["vec_base"]
            root = block_def["root"]
            root_along_vb = sum(r * v for r, v in zip(root, vb))
            block_def["center_offset"] = block_def["root_offset"] + root_along_vb
        block_def.setdefault("collider", None)
        block_def.setdefault("outline", None)
        block_def.setdefault("data", "")
        block_def.setdefault("summary", "")
        block_def.setdefault("description", "")
        block_def.setdefault("wiki", "")
        block_def.setdefault("block_points", 0)
        block_def["cost"] = int(block_def.get("block_points", 0))
        block_def.setdefault("weight", 1.0)
        role = roles.get(block_id)
        if role is None:
            raise ValueError(
                f"Missing role definition for block id {block_id} ('{block_name}') in {roles_path}"
            )
        role_block_name = str(role.get("block_name", "")).strip()
        if role_block_name != block_name:
            raise ValueError(
                f"Role block_name mismatch for id {block_id}: roles='{role_block_name}', runtime='{block_name}'"
            )
        role_type = str(role["type"])
        block_def["type"] = role_type
        block_def.setdefault("mesh_key", dump_name)
        if role_type != "connection" and not block_runtime_mesh_exists(
            mesh_key=block_def["mesh_key"],
            registry_path=registry_path,
        ):
            block_def["disable"] = True
            block_def["enabled"] = False

        block_def["faces"] = _normalize_faces(value=block_def.get("faces"))
        block_def["spin"] = _normalize_spin_config(value=block_def.get("spin", False))
        block_def["shoot"] = bool(block_def.get("shoot", False))
        block_def["locomotion"] = _normalize_optional_dict(value=block_def.get("locomotion", False))
        if role_type == "pointer":
            block_def["pointer_axis"] = _normalize_pointer_axis(
                value=role.get("pointer_axis"),
                block_name=block_name,
            )
        else:
            block_def["pointer_axis"] = False

        block_def["collider_mode"] = block_def.get("collider_mode", defaults.get("collider_mode", "obb"))
        block_def["collider_shrink"] = float(block_def.get("collider_shrink", defaults.get("collider_shrink", 0.85)))
        block_def["prefab"] = block_def.get("prefab")

        authoring_entry = authoring.get(block_id, {})
        authoring_summary = str(authoring_entry.get("summary", "")).strip()
        authoring_description = str(authoring_entry.get("description", "")).strip()
        if authoring_summary != "":
            block_def["summary"] = authoring_summary
        if authoring_description != "":
            block_def["description"] = authoring_description

        key_name = block_name
        if key_name in result:
            key_name = f"{block_name} [{block_id}]"
        result[key_name] = block_def

    return result
