"""The unique public block name is ``block_authoring.toml`` ``block_name``.

That string is the only name MCP, inspect-machine, and controllers may use.
``block_id`` is the internal join key for the game dump, channel catalog, and
roles table. Catalog prefab strings and any other spelling are not names and
must not be accepted as a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .block_authoring import load_block_authoring
from .paths import get_block_authoring_path, resolve_project_path


@dataclass(frozen=True)
class CanonicalBlock:
    block_id: str
    name: str


def _authoring_path(*, authoring_path: str | Path | None = None) -> Path:
    if authoring_path is not None:
        return resolve_project_path(path=authoring_path)
    return get_block_authoring_path()


@lru_cache(maxsize=4)
def load_canonical_blocks(*, authoring_path: str | None = None) -> dict[str, CanonicalBlock]:
    path = _authoring_path(authoring_path=authoring_path)
    authoring = load_block_authoring(authoring_path=path)
    if not authoring:
        raise FileNotFoundError(f"Block authoring file not found or empty: {path}")
    blocks: dict[str, CanonicalBlock] = {}
    for block_id, entry in authoring.items():
        name = str(entry.get("block_name", "")).strip()
        identified = CanonicalBlock(block_id=str(int(block_id)), name=name)
        blocks[identified.block_id] = identified
    return blocks


def canonical_block(
    block_id: str | int, *, authoring_path: str | None = None
) -> CanonicalBlock | None:
    return load_canonical_blocks(authoring_path=authoring_path).get(str(int(block_id)))


def canonical_block_name(
    block_id: str | int, *, authoring_path: str | None = None
) -> str | None:
    block = canonical_block(block_id, authoring_path=authoring_path)
    return None if block is None else block.name


def resolve_block_name(
    *,
    block_id: str | int,
    catalog_name: str | None = None,
    authoring_path: str | None = None,
) -> tuple[str, str]:
    """Return (authored unique name, dump prefab string).

    The prefab string is game-dump metadata, not a name and not a lookup key.
    """
    identified = canonical_block(block_id, authoring_path=authoring_path)
    prefab = "" if catalog_name is None else str(catalog_name).strip()
    if identified is None:
        raise ValueError(
            f"Block id {block_id} has no unique name in block_authoring.toml. "
            "Every public block name must be authored there; dump prefab strings "
            "are not names."
        )
    return (identified.name, prefab)


def block_matches(*, block_name: str, query: str) -> bool:
    """True only when query is exactly the authored unique name."""
    return str(query) == str(block_name)


def select_blocks(blocks: Iterable[object], query: str) -> list[object]:
    """Select blocks by the exact authored unique name."""
    wanted = str(query)
    matched: list[object] = []
    for block in blocks:
        if block_matches(block_name=str(getattr(block, "name")), query=wanted):
            matched.append(block)
    return matched
