"""Build the assembled validation machine from the canonical block registry."""

from __future__ import annotations

import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .definitions_loader import load_runtime_blocks
from .paths import get_block_registry_path, get_saved_machine_dir
from .progress import progress_iter

VALIDATION_MACHINE_NAME = "assembled_validation"

CATEGORY_ORDER = (
    "basic",
    "locomotion",
    "mechanical",
    "weaponry",
    "armour",
    "flight",
    "automation",
    "water",
    "space-flight",
)

EXPECTED_CATEGORY_COUNTS = {
    "basic": 9,
    "locomotion": 12,
    "mechanical": 12,
    "weaponry": 16,
    "armour": 11,
    "flight": 9,
    "automation": 10,
    "water": 8,
    "space-flight": 12,
}

EXCLUDE_IDS: dict[int, str] = {
    57: "Pin is excluded from simulation validation (notebook EXCLUDE_IDS_FROM_RENDER).",
    58: "Camera Block is excluded from simulation validation (notebook EXCLUDE_IDS_FROM_RENDER).",
}


@dataclass(frozen=True)
class RegistryBlock:
    block_id: int
    display_name: str
    category: str
    block_type: str
    enabled: bool


@dataclass(frozen=True)
class ExcludedBlock:
    block_id: int | None
    display_name: str
    reason: str


@dataclass(frozen=True)
class RegistryInspection:
    render_blocks: tuple[RegistryBlock, ...]
    render_blocks_by_category: dict[str, tuple[RegistryBlock, ...]]
    excluded: tuple[ExcludedBlock, ...]
    category_counts: dict[str, int]
    dlc_block_ids: dict[str, tuple[int, ...]]


@dataclass(frozen=True)
class ValidationMachineResult:
    name: str
    bsg_path: Path
    history_path: Path
    included_block_ids: tuple[int, ...]
    excluded: tuple[ExcludedBlock, ...]
    attached_names: tuple[str, ...]


class ValidationMachineError(RuntimeError):
    """The assembled validation machine could not be inspected or built."""


def _runtime_def_categories(*, raw_categories: Any, source: str) -> list[str]:
    if isinstance(raw_categories, str):
        return [raw_categories.lower()]
    if isinstance(raw_categories, list):
        return [str(item).lower() for item in raw_categories]
    raise ValidationMachineError(f"{source} has invalid category {raw_categories!r}.")


def inspect_dlc_block_ids(*, registry_path: Path | None = None) -> dict[str, tuple[int, ...]]:
    """Read water/space-flight IDs from the shipped registry. Does not need Inspector dumps."""
    resolved = get_block_registry_path(registry_path=registry_path)
    with resolved.open("rb") as handle:
        registry_obj = tomllib.load(handle)
    runtime_defs = registry_obj.get("runtime_defs")
    if not isinstance(runtime_defs, dict):
        raise ValidationMachineError(f"{resolved} is missing [runtime_defs].")

    dlc_ids: dict[str, list[int]] = {"water": [], "space-flight": []}
    for raw_key, block_def in runtime_defs.items():
        if not isinstance(block_def, dict):
            raise ValidationMachineError(f"{resolved} runtime_defs[{raw_key!r}] is not a table.")
        if not bool(block_def.get("enabled", True)):
            continue
        block_id = int(raw_key) if str(raw_key).isdigit() else None
        if block_id is None and isinstance(block_def.get("id"), int):
            block_id = int(block_def["id"])
        if block_id is None:
            continue
        for category in _runtime_def_categories(
            raw_categories=block_def.get("category", []),
            source=f"{resolved} runtime_defs[{raw_key!r}]",
        ):
            if category in dlc_ids:
                dlc_ids[category].append(block_id)
    return {category: tuple(sorted(set(ids))) for category, ids in dlc_ids.items()}


def inspect_registry(*, registry_path: Path | None = None) -> RegistryInspection:
    resolved = get_block_registry_path(registry_path=registry_path)
    with resolved.open("rb") as handle:
        registry_obj = tomllib.load(handle)
    runtime_defs = registry_obj.get("runtime_defs")
    if not isinstance(runtime_defs, dict):
        raise ValidationMachineError(f"{resolved} is missing [runtime_defs].")

    runtime_blocks = load_runtime_blocks(registry_path=resolved)
    runtime_by_id: dict[int, dict[str, Any]] = {}
    name_by_id: dict[int, str] = {}
    for name, block_def in runtime_blocks.items():
        block_id = int(block_def["id"])
        runtime_by_id[block_id] = block_def
        name_by_id[block_id] = name

    render: list[RegistryBlock] = []
    excluded: list[ExcludedBlock] = []
    all_counts: Counter[str] = Counter()

    for raw_key, block_def in runtime_defs.items():
        if not isinstance(block_def, dict):
            raise ValidationMachineError(f"{resolved} runtime_defs[{raw_key!r}] is not a table.")
        enabled = bool(block_def.get("enabled", True))
        block_id = int(raw_key) if str(raw_key).isdigit() else None
        categories = _runtime_def_categories(
            raw_categories=block_def.get("category", []),
            source=f"{resolved} runtime_defs[{raw_key!r}]",
        )
        runtime = runtime_by_id.get(block_id) if block_id is not None else None
        block_type = str(runtime.get("type", "")) if runtime else str(block_def.get("type", ""))
        display_name = name_by_id.get(block_id, str(raw_key)) if block_id is not None else str(raw_key)

        for category in categories:
            if category in EXPECTED_CATEGORY_COUNTS:
                all_counts[category] += 1

        tracked = [category for category in categories if category in EXPECTED_CATEGORY_COUNTS]
        if not tracked:
            continue
        if not enabled:
            excluded.append(ExcludedBlock(block_id, display_name, "registry enabled=false"))
            continue
        if block_type == "connection":
            excluded.append(
                ExcludedBlock(block_id, display_name, "type=connection; placed later via connect_blocks")
            )
            continue
        if block_id is not None and block_id in EXCLUDE_IDS:
            excluded.append(ExcludedBlock(block_id, display_name, EXCLUDE_IDS[block_id]))
            continue
        if block_id is None or block_id not in runtime_by_id:
            excluded.append(ExcludedBlock(block_id, display_name, "not loadable in runtime block map"))
            continue
        for category in tracked:
            render.append(
                RegistryBlock(
                    block_id=block_id,
                    display_name=display_name,
                    category=category,
                    block_type=block_type,
                    enabled=True,
                )
            )

    mismatches = [
        f"{category}: expected {EXPECTED_CATEGORY_COUNTS[category]}, actual {all_counts.get(category, 0)}"
        for category in CATEGORY_ORDER
        if int(all_counts.get(category, 0)) != EXPECTED_CATEGORY_COUNTS[category]
    ]
    if mismatches:
        raise ValidationMachineError(
            "Registry category counts do not match the assembled-validation contract: "
            + "; ".join(mismatches)
        )

    by_category: dict[str, list[RegistryBlock]] = defaultdict(list)
    for item in render:
        by_category[item.category].append(item)
    ordered: list[RegistryBlock] = []
    grouped: dict[str, tuple[RegistryBlock, ...]] = {}
    for category in CATEGORY_ORDER:
        grouped[category] = tuple(by_category.get(category, []))
        ordered.extend(grouped[category])

    return RegistryInspection(
        render_blocks=tuple(ordered),
        render_blocks_by_category=grouped,
        excluded=tuple(excluded),
        category_counts={category: int(all_counts.get(category, 0)) for category in CATEGORY_ORDER},
        dlc_block_ids=inspect_dlc_block_ids(registry_path=resolved),
    )


def _place_category_row(
    machine: Any,
    *,
    entries: tuple[RegistryBlock, ...],
    current_height: float,
    spacing: float,
    category_key: str,
) -> tuple[list[str], list[int]]:
    """Notebook ``build_machine_for_category`` loop: one Starting Block pad per
    entry, then attach the target on f4 and f5."""
    attached: list[str] = []
    included_ids: list[int] = []
    x_offset = 0.0
    for entry in progress_iter(
        entries, desc=f"preview {category_key}", unit="block", total=len(entries)
    ):
        display_name = entry.display_name
        local_id = str(machine.uid)
        base_block = machine.blocks_storage.get(
            block_name="Starting Block",
            local_id=local_id,
            note="base block",
        )
        base_block.shift(shift_real=[x_offset, 0.0, current_height])
        collision_msg = machine._add_block(block=base_block, return_summary=True)
        if collision_msg:
            raise ValidationMachineError(
                f"Failed to place Starting Block pad for {display_name!r} at "
                f"x={x_offset}, z={current_height}: {collision_msg}"
            )
        if display_name != "Starting Block":
            machine.attach_block_to(base_block=local_id, face="f4", new_block=display_name)
            machine.attach_block_to(base_block=local_id, face="f5", new_block=display_name)
        attached.append(display_name)
        included_ids.append(entry.block_id)
        x_offset += spacing
    return attached, included_ids


def _attach_preview_all_att(machine: Any) -> int:
    """Notebook ``preview_all_att`` loop: wooden block on every sticky face."""
    blocks_dict = machine.blocks
    if not isinstance(blocks_dict, dict):
        raise TypeError("machine.blocks is not a dict-like container")
    base_block_ids = list(blocks_dict.keys())
    attach_count = 0
    for base_local_id in progress_iter(
        base_block_ids, desc="preview faces", unit="block", total=len(base_block_ids)
    ):
        base_block = blocks_dict.get(base_local_id)
        if base_block is None:
            raise ValidationMachineError(f"Block {base_local_id} not found during face traversal")
        if base_block.name == "Starting Block":
            continue
        attachable_faces = [
            face_name
            for face_name, face_obj in base_block.faces.items()
            if bool(face_obj.sticky)
        ]
        for face_name in attachable_faces:
            before = set(machine.blocks)
            machine.attach_block_to(
                base_block=base_local_id,
                face=face_name,
                new_block="Single Wooden Block",
            )
            if set(machine.blocks) != before:
                attach_count += 1
    return attach_count


def build_validation_machine(
    *,
    output_dir: Path | None = None,
    registry_path: Path | None = None,
    name: str = VALIDATION_MACHINE_NAME,
    inspection: RegistryInspection | None = None,
    spacing: float = 5.0,
) -> ValidationMachineResult:
    from .build import Machine

    resolved_registry = get_block_registry_path(registry_path=registry_path)
    inspected = inspect_registry(registry_path=resolved_registry) if inspection is None else inspection
    destination = Path(output_dir) if output_dir is not None else get_saved_machine_dir()
    destination.mkdir(parents=True, exist_ok=True)

    machine = Machine(
        name="preview_all",
        do_collision=False,
        registry_path=resolved_registry,
        write_full_history=True,
    )
    # Same as block_preview.ipynb: mark started so attach_block_to is allowed,
    # then place every category row with its own Starting Block pads.
    machine.started = True
    machine.note = "Assembled validation machine covering every supported block."

    attached: list[str] = []
    included_ids: list[int] = []
    current_height = 0.0
    for category_key in CATEGORY_ORDER:
        names, ids = _place_category_row(
            machine,
            entries=inspected.render_blocks_by_category.get(category_key, ()),
            current_height=current_height,
            spacing=spacing,
            category_key=category_key,
        )
        attached.extend(names)
        included_ids.extend(ids)
        current_height += spacing

    machine.connect_blocks(block_a="1", block_b="3", face_a="f0", face_b="f7", connector="Brace")
    machine.connect_blocks(block_a="1", block_b="3", face_a="f0", face_b="f2", connector="Spring")
    machine.connect_blocks(block_a="1", block_b="4", face_a="f0", face_b="f3", connector="Rope Winch")
    machine.connect_blocks(block_a="1", block_b="4", face_a="f0", face_b="f8", connector="Rope Measure")
    machine.connect_blocks(block_a="1", block_b="4", face_a="f0", face_b="f9", connector="Fuel Line")

    attach_count = _attach_preview_all_att(machine)
    machine.name = name
    machine.to_file(output_dir=destination)
    bsg_path = destination / f"{name}.bsg"
    history_path = destination / f"{name}.json"
    if not bsg_path.is_file() or not history_path.is_file():
        raise ValidationMachineError(
            f"Machine.to_file() did not write {bsg_path} and {history_path}."
        )
    return ValidationMachineResult(
        name=name,
        bsg_path=bsg_path,
        history_path=history_path,
        included_block_ids=tuple(included_ids),
        excluded=inspected.excluded,
        attached_names=tuple(attached) + (f"Single Wooden Block x{attach_count}",),
    )
