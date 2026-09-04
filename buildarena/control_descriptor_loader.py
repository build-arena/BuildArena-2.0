"""Load authored, user-facing control-channel semantics by block id."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .block_authoring import (
    build_control_descriptor_map_from_authoring,
    load_block_authoring,
)


@dataclass(frozen=True)
class ControlSemantics:
    """Classification of every catalog channel and mapper slider for one block type."""

    descriptions: dict[str, str]
    aliases: dict[str, str]
    ignored: dict[str, str]
    sliders: dict[str, str]


_module_cache: dict[str, ModuleType] = {}


def _load_module(module_name: str) -> ModuleType:
    module = _module_cache.get(module_name)
    if module is None:
        module = importlib.import_module(f"blocks.control_descriptors.{module_name}")
        _module_cache[module_name] = module
    return module


def load_control_semantics(
    *,
    block_id: int,
    authoring_path: Path | None = None,
) -> ControlSemantics | None:
    """Load and validate one block type's authored control semantics."""
    authoring = load_block_authoring(authoring_path=authoring_path)
    module_name = build_control_descriptor_map_from_authoring(authoring=authoring).get(
        block_id
    )
    if module_name is None:
        return None

    module = _load_module(module_name)
    all_semantics = getattr(module, "CONTROL_SEMANTICS", None)
    if all_semantics is not None:
        raw = all_semantics.get(block_id)
        if raw is None:
            raise KeyError(
                f"Control descriptor module {module_name!r} has no entry for "
                f"block id {block_id}."
            )
        descriptions = dict(raw.get("descriptions", {}))
        aliases = dict(raw.get("aliases", {}))
        ignored = dict(raw.get("ignored", {}))
        sliders = dict(raw.get("sliders", {}))
    else:
        descriptions = dict(getattr(module, "CHANNEL_DESCRIPTIONS", {}))
        aliases = dict(getattr(module, "CHANNEL_ALIASES", {}))
        ignored = dict(getattr(module, "IGNORED_CHANNELS", {}))
        sliders = dict(getattr(module, "SLIDER_DESCRIPTIONS", {}))
    overlap = (
        (set(descriptions) & set(aliases))
        | (set(descriptions) & set(ignored))
        | (set(aliases) & set(ignored))
    )
    if overlap:
        raise ValueError(
            f"Control descriptor {module_name!r} classifies channels more than once: "
            f"{sorted(overlap)}."
        )
    slider_names = {str(key) for key in sliders}
    if slider_names & set(aliases):
        raise ValueError(
            f"Control descriptor {module_name!r} uses the same string as a slider "
            f"and a channel alias: {sorted(slider_names & set(aliases))}."
        )
    return ControlSemantics(
        descriptions={str(key): str(value) for key, value in descriptions.items()},
        aliases={str(key): str(value) for key, value in aliases.items()},
        ignored={str(key): str(value) for key, value in ignored.items()},
        sliders={str(key): str(value) for key, value in sliders.items()},
    )

