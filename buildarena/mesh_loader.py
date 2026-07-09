import tomllib
from pathlib import Path

import numpy as np
import quaternion
import trimesh
from trimesh.visual.texture import TextureVisuals

from PIL import Image

from .paths import get_besiege_data_path, get_block_registry_path, get_skin_dir

# ── path-keyed caches (dict mutation, no ``global`` needed) ───────────
_registry_cache: dict[str, dict] = {}
_mesh_cache: dict[str, trimesh.Trimesh] = {}
DEFAULT_REGISTRY_PATH = get_block_registry_path()

def load_registry(*, registry_path: str | Path | None = None) -> dict:
    resolved_registry_path = get_block_registry_path(registry_path=registry_path)
    cache_key = str(resolved_registry_path.resolve())
    if cache_key in _registry_cache:
        return _registry_cache[cache_key]
    with open(resolved_registry_path, "rb") as f:
        loaded = tomllib.load(f)
    _registry_cache[cache_key] = loaded
    return loaded


def is_connector(mesh_key: str, *, registry_path: str | Path | None = None) -> bool:
    registry = load_registry(registry_path=registry_path)
    return mesh_key in registry.get("connectors", {})


def load_block_mesh(
    mesh_key: str,
    *,
    data_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> trimesh.Trimesh:
    """Load the raw OBJ mesh for a block from the game's Skins directory.

    Raises
    ------
    RuntimeError
        If BESIEGE_DATA_PATH is not configured.
    FileNotFoundError
        If the block's skin directory or OBJ file cannot be found.
    """
    resolved_registry_path = get_block_registry_path(registry_path=registry_path)
    resolved_data_path = get_besiege_data_path(data_path=data_path)
    cache_key = "::".join(
        [
            str(resolved_data_path.resolve()),
            str(resolved_registry_path.resolve()),
            mesh_key,
        ]
    )
    if cache_key in _mesh_cache:
        return _mesh_cache[cache_key].copy()

    registry = load_registry(registry_path=resolved_registry_path)
    block_info = registry.get("blocks", {}).get(mesh_key, {})
    if not block_info.get("enabled", True):
        raise FileNotFoundError(
            f"Block '{mesh_key}' is disabled in the registry"
        )

    skin_set = registry.get("skin_set", "Template")
    skin_dir = get_skin_dir(
        mesh_key=mesh_key,
        skin_set=skin_set,
        data_path=resolved_data_path,
    )

    obj_file = block_info.get("obj_file")
    if obj_file is not None:
        obj_path = skin_dir / obj_file
    else:
        obj_path = _discover_obj_file(skin_dir=skin_dir, mesh_key=mesh_key)

    mesh = trimesh.load(file_obj=str(obj_path), force="mesh")
    mesh = _attach_real_skin_if_needed(mesh=mesh, mesh_dir=obj_path.parent)

    _mesh_cache[cache_key] = mesh
    return mesh.copy()


def _discover_obj_file(*, skin_dir: Path, mesh_key: str) -> Path:
    """Find the OBJ file inside a block's skin directory."""
    if not skin_dir.is_dir():
        raise FileNotFoundError(
            f"Skin directory not found for '{mesh_key}': {skin_dir}"
        )
    obj_files = sorted(
        path for path in skin_dir.iterdir() if path.suffix.lower() == ".obj"
    )
    if len(obj_files) == 0:
        raise FileNotFoundError(
            f"No .obj file found in skin directory for '{mesh_key}': {skin_dir}"
        )
    return obj_files[0]


def block_runtime_mesh_exists(
    *,
    mesh_key: str | None,
    data_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> bool:
    """Return whether the local Besiege install has a runtime OBJ for a block.

    Connectors do not have visual meshes, so callers should treat them as
    available before calling this helper.
    """
    if mesh_key is None:
        return False

    resolved_registry_path = get_block_registry_path(registry_path=registry_path)
    resolved_data_path = get_besiege_data_path(data_path=data_path)
    registry = load_registry(registry_path=resolved_registry_path)
    block_info = registry.get("blocks", {}).get(mesh_key, {})
    if not block_info.get("enabled", True):
        return False

    skin_set = registry.get("skin_set", "Template")
    skin_dir = get_skin_dir(
        mesh_key=mesh_key,
        skin_set=skin_set,
        data_path=resolved_data_path,
    )
    if not skin_dir.is_dir():
        return False

    obj_file = block_info.get("obj_file")
    if obj_file is not None:
        return (skin_dir / obj_file).is_file()

    return any(path.suffix.lower() == ".obj" for path in skin_dir.iterdir())


def _attach_real_skin_if_needed(*, mesh: trimesh.Trimesh, mesh_dir: Path) -> trimesh.Trimesh:
    """Some OBJ loads fall back to a tiny placeholder texture (e.g. 2x2).
    If detected, attach the first PNG from the mesh folder as skin."""
    if not hasattr(mesh.visual, "uv"):
        return mesh
    if mesh.visual.uv is None:
        return mesh

    mat = getattr(mesh.visual, "material", None)
    img = getattr(mat, "image", None) if mat is not None else None
    if img is not None and getattr(img, "size", (0, 0))[0] > 4 and getattr(img, "size", (0, 0))[1] > 4:
        return mesh

    png_candidates = sorted(
        path for path in mesh_dir.iterdir() if path.suffix.lower() == ".png"
    )

    if len(png_candidates) == 0:
        return mesh

    skin_image = Image.open(fp=png_candidates[0]).convert(mode="RGBA")

    mesh.visual = TextureVisuals(uv=mesh.visual.uv, image=skin_image)
    return mesh


def load_game_mesh(
    mesh_key: str | None,
    *,
    data_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> trimesh.Trimesh | None:
    """Load a raw game mesh for a block.

    Returns *None* only for connectors (they have no visual mesh).
    Raises on any other failure (missing data path, missing file, etc.).
    """
    if mesh_key is None:
        raise ValueError("mesh_key must not be None for non-connection blocks")
    if is_connector(mesh_key, registry_path=registry_path):
        return None
    return load_block_mesh(mesh_key=mesh_key, data_path=data_path, registry_path=registry_path)


def normalize_game_mesh(
    *,
    mesh: trimesh.Trimesh,
    visual_transform: dict,
) -> trimesh.Trimesh:
    """Apply block-local transform chain to a raw OBJ mesh.

    Source is ``visual_transform`` extracted from collider dump.
    Chain: local_scale → local_rotation (quaternion) → local_translation.
    """
    result = mesh.copy()

    local_scale = np.asarray(visual_transform["local_scale"], dtype=np.float64)
    local_rotation = visual_transform["local_rotation"]
    local_translation = np.asarray(visual_transform["local_translation"], dtype=np.float64)

    result.vertices *= local_scale

    q = quaternion.quaternion(local_rotation[0], local_rotation[1],
                              local_rotation[2], local_rotation[3])
    q = q.normalized()
    rot_mat = quaternion.as_rotation_matrix(q)
    T_rot = np.eye(4, dtype=np.float64)
    T_rot[:3, :3] = rot_mat
    result.apply_transform(T_rot)

    result.apply_translation(local_translation)

    return result


def load_aligned_game_mesh(
    *,
    mesh_key: str | None,
    visual_transform: dict | None = None,
    data_path: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> trimesh.Trimesh | None:
    """Load an OBJ mesh and align it using dump visual transform data.

    Returns *None* only for connectors.  Raises on any other failure.
    """
    if mesh_key is None:
        raise ValueError("mesh_key must not be None for non-connection blocks")
    if is_connector(mesh_key, registry_path=registry_path):
        return None
    if visual_transform is None:
        raise ValueError(
            f"visual_transform is required for non-connection block '{mesh_key}'"
        )

    raw_mesh = load_block_mesh(mesh_key=mesh_key, data_path=data_path, registry_path=registry_path)
    return normalize_game_mesh(
        mesh=raw_mesh,
        visual_transform=visual_transform,
    )
