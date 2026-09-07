"""Combined structure + control-channel inspection for MCP-built machines.

`inspect-machine` only accepts the paired output of an MCP build
(``besiege/build.py``'s ``to_file`` writes ``<name>.bsg`` plus the
``<name>.json`` operation history side by side). The history is replayed
into a live ``buildarena.build.Machine`` whose per-block captions carry the
structural description (position, descriptor, spin, notes); the BSG
provides the persisted block guids and the control channels. A BSG without
its same-name JSON is not inspectable by design: hand-built machines have
no build history, so there is no structural description to combine (use
``dump-channels`` for their channels and ``configure-telemetry`` for
telemetry recording).

Guid correspondence: replaying the history creates fresh ``uuid4`` guids,
so replayed blocks are matched to BSG blocks purely by order. ``to_xml``
writes the Starting Block first and every other block in build (insertion)
order, which this module mirrors; any count or per-position block-id
mismatch is a hard error, never a partial match.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .machine import BlockInstance, ControlChannel, channel_detail_lines

if TYPE_CHECKING:  # buildarena.build is imported lazily inside load_build_module
    from buildarena.build import Block, Machine

# besiege_cli runs from control/ by convention; the root `buildarena` package
# and all of its data files (block registry, roles, meshes, collider dumps)
# resolve relative to the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def locate_operation_history(bsg_path: str | Path) -> Path:
    """Return the MCP operation-history JSON saved next to ``bsg_path``.

    The path is resolved to absolute first: relative paths break under the
    cwd switch to the repo root during replay, and unresolved `..` segments
    can push a deep path over the Windows MAX_PATH limit, making existing
    files test as missing.
    """
    json_path = Path(bsg_path).resolve().with_suffix(".json")
    if not json_path.is_file():
        raise FileNotFoundError(
            f"inspect-machine requires the MCP build history next to the BSG, but {json_path} does "
            "not exist. Only MCP-built machines (saved via save_machine, which writes <name>.bsg + "
            "<name>.json together) can be inspected. For a hand-built BSG use `dump-channels` to "
            "export its control channels and `configure-telemetry` to set up telemetry recording."
        )
    return json_path


def load_build_module() -> Any:
    """Import the root ``buildarena.build`` module.

    The import itself loads the block registry through cwd-relative paths,
    so the working directory is switched to the repository root for the
    duration of the import and restored afterwards.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    with contextlib.chdir(REPO_ROOT):
        import buildarena.build as build_module
    return build_module


def rebuild_machine(json_path: str | Path) -> "Machine":
    """Replay an MCP operation history into a ``buildarena.build.Machine``.

    Reuses ``Machine.from_file`` (load history + rebuild). The replay runs
    with cwd at the repository root because block meshes / collider dumps
    load through cwd-relative paths; ``write_full_history=False`` makes the
    replay side-effect free on disk.
    """
    resolved_json = Path(json_path).resolve()
    build_module = load_build_module()
    with contextlib.chdir(REPO_ROOT):
        machine = build_module.Machine(
            name=resolved_json.stem,
            save_dir=str(resolved_json.parent),
            write_full_history=False,
        )
        history = machine.load_operation_history(resolved_json)
        if not history or history[0].get("op") != "start":
            first_op = history[0].get("op") if history else "<empty>"
            raise ValueError(
                f"{resolved_json} is not a plain MCP machine history: it begins with operation "
                f"{first_op!r} instead of 'start'. Assembly histories are not supported by "
                "inspect-machine."
            )
        machine.from_file(resolved_json)
    return machine


def align_blocks(machine: "Machine", bsg_blocks: list[BlockInstance]) -> list[tuple[BlockInstance, "Block"]]:
    """Pair BSG blocks (file order) with replayed blocks (BSG write order).

    Mirrors ``Machine.to_xml``: the Starting Block is written first, then
    every other block in insertion order. Each pair must agree on the block
    type id; any mismatch means the JSON and BSG are not the same machine.
    """
    build_blocks = list(machine.blocks.values())
    starting = [block for block in build_blocks if block.name == "Starting Block"]
    if len(starting) != 1:
        raise ValueError(
            f"Replayed machine has {len(starting)} Starting Blocks (expected exactly 1); "
            "the operation history is not a valid MCP machine build."
        )
    ordered = starting + [block for block in build_blocks if block is not starting[0]]
    if len(ordered) != len(bsg_blocks):
        raise ValueError(
            f"JSON does not correspond to this BSG: the replayed machine has {len(ordered)} "
            f"blocks but the BSG has {len(bsg_blocks)}. The .bsg and .json were not saved "
            "together by the same MCP build."
        )
    pairs: list[tuple[BlockInstance, Any]] = []
    for bsg_block, build_block in zip(bsg_blocks, ordered):
        if str(build_block.id) != str(bsg_block.block_id):
            raise ValueError(
                f"JSON does not correspond to this BSG: at position {bsg_block.local_index} the "
                f"BSG carries block id {bsg_block.block_id!r} but the replayed machine has "
                f"{build_block.id!r} ({build_block.name}). The .bsg and .json were not saved "
                "together by the same MCP build."
            )
        if build_block.name != bsg_block.name:
            raise ValueError(
                f"Block name is not aligned at inspect index {bsg_block.local_index}: "
                f"MCP replay has {build_block.name!r} but the BSG resolved "
                f"{bsg_block.name!r} (id={bsg_block.block_id}). "
                "The unique public name is block_authoring.toml block_name; "
                "prefab strings and other spellings are not names."
            )
        pairs.append((bsg_block, build_block))
    return pairs


def machine_inspect_report(
    machine: "Machine",
    bsg_blocks: list[BlockInstance],
    channels: list[ControlChannel],
    *,
    sliders: list | None = None,
    verbose: bool = False,
) -> str:
    """Render the combined structural + control-channel description.

    Blocks appear in BSG file order. Each entry is the replayed block's own
    caption (the same structural text the MCP builder shows) followed by a
    user-facing description of that block's named controls. Protocol
    details (guid, KeyList position, activation implementation) are only
    included in verbose mode.
    """
    pairs = align_blocks(machine, bsg_blocks)

    from buildarena.control_descriptor_loader import load_control_semantics

    channels_by_index: dict[int, list[ControlChannel]] = {}
    for channel in channels:
        channels_by_index.setdefault(channel.local_index, []).append(channel)

    user_controls_by_index: dict[int, list[tuple[ControlChannel, str]]] = {}
    authored_sliders_by_index: dict[int, list[tuple[str, str]]] = {}
    sliders_by_index: dict[int, list] = {}
    for slider in sliders or []:
        sliders_by_index.setdefault(int(slider.local_index), []).append(slider)
    for bsg_block, build_block in pairs:
        semantics = load_control_semantics(
            block_id=int(build_block.id),
            authoring_path=REPO_ROOT / "blocks" / "block_authoring.toml",
        )
        block_channels = channels_by_index.get(bsg_block.local_index, [])
        catalog_sliders = sliders_by_index.get(bsg_block.local_index, [])
        if semantics is None and (block_channels or catalog_sliders):
            raise ValueError(
                f"Block {bsg_block.local_index} ({build_block.name}, id={build_block.id}) "
                "has control channels or catalog sliders but no authored control_descriptor."
            )
        if semantics is None:
            user_controls_by_index[bsg_block.local_index] = []
            authored_sliders_by_index[bsg_block.local_index] = []
            continue
        by_name = {channel.channel: channel for channel in block_channels}
        classified = (
            set(semantics.descriptions)
            | set(semantics.aliases)
            | set(semantics.ignored)
        )
        unclassified = sorted(set(by_name) - classified)
        if unclassified:
            raise ValueError(
                f"Block {bsg_block.local_index} ({build_block.name}) has channels without "
                f"authored semantics: {unclassified}."
            )
        user_controls_by_index[bsg_block.local_index] = [
            (by_name[name], description)
            for name, description in semantics.descriptions.items()
            if name in by_name
        ]
        authored = [
            (name, description) for name, description in semantics.sliders.items()
        ]
        catalog_names = {str(slider.name) for slider in catalog_sliders}
        authored_names = {name for name, _description in authored}
        extra = sorted(catalog_names - authored_names)
        if extra:
            raise ValueError(
                f"Block {bsg_block.local_index} ({build_block.name}) has catalog "
                f"sliders that are not authored unique names: {extra}."
            )
        authored_sliders_by_index[bsg_block.local_index] = authored

    named_channel_count = sum(len(items) for items in user_controls_by_index.values())
    authored_slider_count = sum(len(items) for items in authored_sliders_by_index.values())
    lines: list[str] = []
    if machine.note:
        lines.append(f"Machine Summary: {machine.note}")
    lines.append(
        f"Existing Blocks: {len(pairs)}; named control channels: {named_channel_count}; "
        f"authored sliders: {authored_slider_count}"
    )
    first_control = next(
        (
            (index, channel)
            for index, items in user_controls_by_index.items()
            for channel, _description in items
        ),
        None,
    )
    first_slider = next(
        (
            (index, name)
            for index, items in authored_sliders_by_index.items()
            for name, _description in items
        ),
        None,
    )
    lines.append("")
    if first_control is None and first_slider is None:
        lines.append("This machine has no user-controllable channels or sliders.")
    else:
        lines.append(
            "How to control this machine (value 1.0 presses a channel, 0.0 releases it;"
        )
        lines.append("slider values are the live mapper range, not 0/1):")
        if first_control is not None:
            example_index, example_channel = first_control
            lines.extend(
                [
                    f'  timeline event:  {{"time": 0.5, "block": {example_index}, '
                    f'"channel": "{example_channel.channel}", "value": 1.0}}',
                    f"  python (SDK):    client.send_channels("
                    f'channels=[({example_index}, "{example_channel.channel}")])',
                ]
            )
        if first_slider is not None:
            slider_index, slider_name = first_slider
            lines.append(
                f'  python (SDK):    client.send_sliders('
                f'sliders=[({slider_index}, "{slider_name}", value)])'
            )

    # Captions read repository-relative descriptor content, hence the cwd
    # switch (restored on exit) for the rendering pass as well.
    with contextlib.chdir(REPO_ROOT):
        for bsg_block, build_block in pairs:
            lines.append("")
            lines.append(
                f"[block {bsg_block.local_index}] {build_block.name}  id={bsg_block.block_id}"
            )
            lines.append(build_block.caption(finished=True))
            user_controls = user_controls_by_index[bsg_block.local_index]
            if user_controls:
                lines.append(f"Controls ({len(user_controls)}):")
                for ordinal, (channel, description) in enumerate(
                    user_controls, start=1
                ):
                    lines.append(f"  {ordinal}. Channel name: {channel.channel}")
                    lines.append(f"     Function: {description}")
                    lines.append(f"     Runtime address: KeyList[{channel.keylist_index}]")
                    if channel.aliases:
                        lines.append(f"     Accepted aliases: {', '.join(channel.aliases)}")
                    lines.append(
                        f'     Address: send_channels(channels=[({bsg_block.local_index}, '
                        f'"{channel.channel}")])'
                    )
            else:
                lines.append("Controls: none")
            authored_sliders = authored_sliders_by_index[bsg_block.local_index]
            catalog_by_name = {
                str(slider.name): slider
                for slider in sliders_by_index.get(bsg_block.local_index, [])
            }
            if authored_sliders:
                lines.append(f"Sliders ({len(authored_sliders)}):")
                for ordinal, (name, description) in enumerate(authored_sliders, start=1):
                    catalog_slider = catalog_by_name.get(name)
                    bounds = ""
                    default = ""
                    if catalog_slider is not None:
                        if (
                            catalog_slider.minimum is not None
                            and catalog_slider.maximum is not None
                        ):
                            bounds = f" [{catalog_slider.minimum}, {catalog_slider.maximum}]"
                        if catalog_slider.default is not None:
                            default = f" default={catalog_slider.default}"
                    lines.append(f"  {ordinal}. Slider name: {name}{bounds}{default}")
                    lines.append(f"     Function: {description}")
                    lines.append(
                        f'     Address: send_sliders(sliders=[({bsg_block.local_index}, '
                        f'"{name}", value)])'
                    )
            else:
                lines.append("Sliders: none")

            if verbose:
                lines.append(f"    verbose: guid={bsg_block.guid or '<missing>'}")
                lines.extend(
                    channel_detail_lines(
                        [channel for channel, _description in user_controls]
                    )
                )
    return "\n".join(lines)
