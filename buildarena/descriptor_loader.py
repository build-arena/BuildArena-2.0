from __future__ import annotations

import importlib
import types
from pathlib import Path
from typing import Any, Callable

from .block_authoring import (
    DEFAULT_BLOCK_AUTHORING_PATH,
    build_descriptor_map_from_authoring,
    load_block_authoring,
)

_descriptor_map_cache: dict[str, dict[int, str]] = {}
_descriptor_func_cache: dict[str, Callable[..., str]] = {}


def load_descriptor_map(
    *,
    descriptor_map_path: Path = DEFAULT_BLOCK_AUTHORING_PATH,
) -> dict[int, str]:
    cache_key = str(descriptor_map_path.resolve())
    if cache_key in _descriptor_map_cache:
        return _descriptor_map_cache[cache_key]

    authoring = load_block_authoring(authoring_path=descriptor_map_path)
    descriptor_map = build_descriptor_map_from_authoring(authoring=authoring)

    _descriptor_map_cache[cache_key] = descriptor_map
    return descriptor_map


def _load_descriptor_function(*, module_name: str) -> Callable[..., str]:
    if module_name in _descriptor_func_cache:
        return _descriptor_func_cache[module_name]

    module = importlib.import_module(f"blocks.descriptors.{module_name}")
    descriptor_func = getattr(module, "descriptor", None)
    if descriptor_func is None:
        raise AttributeError(f"Descriptor module '{module_name}' has no descriptor() function.")

    _descriptor_func_cache[module_name] = descriptor_func
    return descriptor_func


def bind_block_descriptor(
    *,
    block: Any,
    block_id: int,
    descriptor_map_path: Path = DEFAULT_BLOCK_AUTHORING_PATH,
) -> Callable[..., str] | None:
    descriptor_map = load_descriptor_map(descriptor_map_path=descriptor_map_path)
    module_name = descriptor_map.get(block_id)
    if module_name is None:
        return None

    descriptor_func = _load_descriptor_function(module_name=module_name)
    return types.MethodType(descriptor_func, block)
