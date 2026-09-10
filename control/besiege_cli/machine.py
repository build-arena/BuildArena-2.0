"""BSG machine parsing, control-channel resolution, and prepared-BSG
generation for the unified `besiege_cli run` workflow.

A prepared BSG is a temporary copy of the user's machine that carries
exactly two additions: the BuildArenaToolKit requiredMods declaration and
a unique ``controller.run_id`` machine-data string. The run id is the only
binding between the machine and its run manifest / timeline / live actions;
no control content, timeline path, or sample rate is ever written into the
BSG. The original file is never modified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from buildarena.block_identity import canonical_block, resolve_block_name, select_blocks
from buildarena.control_descriptor_loader import load_control_semantics
from blocks.control_descriptors.keylist_bindings import KEYLIST_BINDING_VERSION

# Mod identity comes from the single Python source of truth in
# controller_sdk.protocol; nothing here re-declares IDs or versions.
from controller_sdk.protocol import (
    DEFAULT_TELEMETRY_PROFILE,
    TELEMETRY_ALL_TARGETS_TOKEN,
    TELEMETRY_ENABLED_KEY,
    TELEMETRY_MACHINE_SHA256_KEY,
    TELEMETRY_MANAGED_KEY,
    TELEMETRY_OUTPUT_BASENAME_KEY,
    TELEMETRY_PROFILE_KEY,
    TELEMETRY_RUN_ID_KEY,
    TELEMETRY_SAMPLE_RATE_HZ,
    TELEMETRY_SAMPLE_RATE_KEY,
    TELEMETRY_SOURCE_SHA256_KEY,
    TELEMETRY_TARGET_GUIDS_KEY,
    TOOLKIT_MOD_ENTRY,
    TOOLKIT_MOD_ID,
    normalize_telemetry_target_guids,
)

RUN_ID_MACHINE_DATA_KEY = "controller.run_id"


@dataclass(frozen=True)
class BlockInstance:
    local_index: int
    block_id: str
    guid: str
    name: str
    data: dict[str, str]
    catalog_name: str = ""


@dataclass(frozen=True)
class ControlChannel:
    channel_id: str
    block_guid: str
    block_id: str
    block_name: str
    channel: str
    keys: tuple[str, ...]
    mode: str = "hold"
    source: str = "unspecified"
    local_index: int = -1
    # "field", "property", or None (manual/native-only channels, which have
    # no reflected member to resolve). Tells the controller mod whether to
    # activate `channel` via the generated compile-time member switch on
    # the live block instance, instead of matching against a keyboard key.
    semantic_member_kind: str | None = None
    # Position of this channel's MKey within its block's own KeyList
    # (CatalogChannelSpec.channel_index), or -1 for channels with no catalog
    # origin (source="manual"). This is the controller mod's
    # KeyList[keylist_index] activation address: unambiguous even for
    # private-field-only MKey members (no public accessor at all, so
    # semantic_member_kind resolution can never reach them) and immune to
    # keyboard remapping, unlike matching against `keys`.
    keylist_index: int = -1
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogChannelSpec:
    channel_index: int
    semantic_name: str | None
    # "field" or "property"; tells the controller mod whether the semantic
    # name resolves to a field or a property member. None when
    # semantic_name is None.
    semantic_member_kind: str | None
    native_keys: tuple[str, ...]
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogSliderSpec:
    name: str
    minimum: float | None = None
    maximum: float | None = None
    default: float | None = None


@dataclass(frozen=True)
class CatalogBlockEntry:
    block_id: str
    block_name: str
    prefab_name: str
    behaviour_type_full_name: str
    channels: tuple[CatalogChannelSpec, ...]
    sliders: tuple[CatalogSliderSpec, ...] = ()


def guids_for_block_indices(
    blocks: Iterable[BlockInstance],
    indices: Iterable[int],
) -> list[str]:
    """Translate user-facing BSG block indices to protocol guids.

    Every requested index must exist and carry a guid. Results preserve
    first-seen order and remove exact duplicates.
    """
    block_list = list(blocks)
    by_index = {block.local_index: block for block in block_list}
    resolved: list[str] = []
    for index in indices:
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError(f"Block index must be an integer, got {index!r}.")
        block = by_index.get(index)
        if block is None:
            available = ", ".join(str(value) for value in sorted(by_index))
            raise ValueError(
                f"No block at index {index}. Available block indices: {available or '(none)'}."
            )
        if not block.guid:
            raise ValueError(
                f"Block {index} ({block.name}, id={block.block_id}) has no guid and cannot "
                "be addressed. Save the machine in Besiege to assign one first."
            )
        if block.guid not in resolved:
            resolved.append(block.guid)
    return resolved


def load_block_channel_catalog(path: str | Path) -> dict[str, CatalogBlockEntry]:
    catalog_path = Path(path)
    if not catalog_path.exists():
        raise FileNotFoundError(
            f"Block channel catalog not found at {catalog_path}. Run scripts/setup.py to write it "
            "into the ToolKit data directory, or pass --catalog <path>. "
            "No channel-address fallback is used."
        )
    payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    if payload.get("schema") != "buildarena.block_channel_catalog.v1":
        raise ValueError(f"Unsupported channel catalog schema in {catalog_path}.")
    if payload.get("keylist_binding_version", KEYLIST_BINDING_VERSION) != KEYLIST_BINDING_VERSION:
        raise ValueError(f"Unsupported KeyList binding version in {catalog_path}; regenerate the catalog.")
    entries: dict[str, CatalogBlockEntry] = {}
    for block in payload["blocks"]:
        block_id = str(block["block_id"])
        if canonical_block(block_id) is None:
            continue
        semantics = load_control_semantics(block_id=int(block_id))
        channel_specs: list[CatalogChannelSpec] = []
        for channel in block.get("channels", []):
            slot = channel["channel_index"]
            if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
                raise ValueError(f"Block {block_id} has an invalid KeyList slot {slot!r}.")
            names = semantics.channel_names(slot) if semantics is not None else ()
            semantic_name = channel.get("semantic_name")
            if names and semantic_name not in (None, *names, f"keylist_{slot}", f"channel_{slot}"):
                raise ValueError(
                    f"Block {block_id} KeyList[{slot}] catalog name {semantic_name!r} "
                    f"conflicts with {names[0]!r}; regenerate the catalog."
                )
            native_keys_raw = channel.get("native_keys")
            native_keys = tuple(native_keys_raw.split("|")) if native_keys_raw else ()
            channel_specs.append(
                CatalogChannelSpec(
                    channel_index=slot,
                    semantic_name=names[0] if names else semantic_name,
                    semantic_member_kind=channel.get("semantic_member_kind"),
                    native_keys=native_keys,
                    aliases=names[1:],
                )
            )
        slots = [channel.channel_index for channel in channel_specs]
        if len(set(slots)) != len(slots):
            raise ValueError(f"Block {block_id} has duplicate KeyList slots in {catalog_path}.")
        if semantics is not None and set(slots) != set(semantics.keylist_indices.values()):
            raise ValueError(
                f"Block {block_id} catalog slots {sorted(slots)} do not match explicit bindings "
                f"{sorted(semantics.keylist_indices.values())}; regenerate the catalog."
            )
        sliders: list[CatalogSliderSpec] = []
        for slider in block.get("sliders", []) or []:
            if not isinstance(slider, dict) or not str(slider.get("name", "")).strip():
                raise ValueError(
                    f"{catalog_path} block {block.get('block_id')!r} has a slider "
                    "without a non-empty name."
                )
            sliders.append(
                CatalogSliderSpec(
                    name=str(slider["name"]),
                    minimum=_optional_float(slider.get("minimum")),
                    maximum=_optional_float(slider.get("maximum")),
                    default=_optional_float(slider.get("default")),
                )
            )
        block_id = str(block["block_id"])
        prefab_name = str(block["block_name"])
        if canonical_block(block_id) is None:
            # Dump leftovers (Unused, Scaling Block, scrapped ids) are not
            # public blocks and have no authored unique name.
            continue
        canonical_name, aligned_prefab = resolve_block_name(
            block_id=block_id, catalog_name=prefab_name
        )
        entries[block_id] = CatalogBlockEntry(
            block_id=block_id,
            block_name=canonical_name,
            prefab_name=aligned_prefab,
            behaviour_type_full_name=str(block.get("behaviour_type_full_name", "")),
            channels=tuple(channel_specs),
            sliders=tuple(sliders),
        )
    return entries


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _data_dict(data_node: ET.Element | None) -> dict[str, str]:
    if data_node is None:
        return {}
    result: dict[str, str] = {}
    for child in data_node:
        key = child.attrib.get("key")
        if key:
            result[key] = child.text or ""
    return result


def parse_bsg(
    path: str | Path,
    catalog_path: str | Path | None = None,
) -> tuple[ET.ElementTree, list[BlockInstance], dict[str, str]]:
    """Parse a .bsg file into its machine data and block instances.

    Block names are the unique authored name from
    ``blocks/block_authoring.toml``. Catalog prefab strings are dump
    metadata, not names. A block id without an authored name raises.
    ``catalog_path=None`` skips catalog lookup (tests and prepare paths
    that do not need names). Callers that need names must pass a path
    from ``get_block_channel_catalog_path()``.
    """
    bsg_path = Path(path)
    tree = ET.parse(bsg_path)
    root = tree.getroot()
    machine_data = _data_dict(root.find("Data"))
    catalog = load_block_channel_catalog(catalog_path) if catalog_path is not None else {}
    blocks: list[BlockInstance] = []
    blocks_node = root.find("Blocks")
    if blocks_node is None:
        return tree, blocks, machine_data
    for index, block in enumerate(blocks_node.findall("Block")):
        block_id = block.attrib.get("id", "")
        guid = block.attrib.get("guid", "")
        catalog_entry = catalog.get(block_id)
        if catalog_entry is not None:
            name = catalog_entry.block_name
            catalog_name = catalog_entry.prefab_name
        else:
            name, catalog_name = resolve_block_name(block_id=block_id, catalog_name=None)
        blocks.append(
            BlockInstance(
                local_index=index,
                block_id=block_id,
                guid=guid,
                name=name,
                data=_data_dict(block.find("Data")),
                catalog_name=catalog_name,
            )
        )
    return tree, blocks, machine_data


def blocks_named(blocks: Iterable[BlockInstance], query: str) -> list[BlockInstance]:
    """Select blocks by the exact authored unique name."""
    return list(select_blocks(blocks, query))


def _split_manual_keys(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item))
    text = str(value).strip()
    if not text:
        return ()
    return tuple(piece for piece in re.split(r"[\s,;|/]+", text) if piece)


def _load_manual_specs(manual_path: str | Path | None) -> list[dict[str, object]]:
    if manual_path is None:
        return []
    path = Path(manual_path)
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    with Path(manual_path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return list(payload.get("channels", payload if isinstance(payload, list) else []))


def _manual_channels(blocks: Iterable[BlockInstance], manual_path: str | Path | None) -> list[ControlChannel]:
    specs = _load_manual_specs(manual_path)
    channels: list[ControlChannel] = []
    for spec in specs:
        for block in blocks:
            spec_guid = str(spec.get("block_guid", "") or "")
            spec_local_index = spec.get("local_index")
            guid_matches = not spec_guid or spec_guid == "00000000-0000-0000-0000-000000000000" or spec_guid == block.guid
            index_matches = spec_local_index in {None, ""} or int(float(str(spec_local_index))) == block.local_index
            id_matches = str(spec.get("block_id", block.block_id)) == block.block_id
            # Deliberately not requiring block_name equality: block_id (intrinsic
            # to the BSG) plus guid/local_index is already a unique, reliable
            # match. Requiring name equality would re-couple this to whichever
            # source (catalog, runtime export, ...) happened to produce a given
            # block's display name, which is exactly the fragile hardcoded
            # coupling the catalog replaces.
            block_matches = (guid_matches or index_matches) and id_matches
            if not block_matches:
                continue
            channel = str(spec.get("channel", "") or "")
            if not channel:
                # The mod's runtime control_channels.csv has no `channel`
                # column; its channel_id is "<block_guid>:<channel_name>", so
                # recover the bare channel name from it.
                channel_id_value = str(spec.get("channel_id", "") or "")
                prefix = f"{block.guid}:"
                if channel_id_value.startswith(prefix):
                    channel = channel_id_value[len(prefix):]
                else:
                    channel = channel_id_value or "control"
            keys = _split_manual_keys(spec.get("keys", spec.get("key", "")))
            if isinstance(spec.get("key"), str):
                keys = (str(spec["key"]),)
            manual_keylist_index = spec.get("keylist_index", -1)
            channels.append(
                ControlChannel(
                    channel_id=str(spec.get("channel_id", f"{block.guid}:{channel}")),
                    block_guid=block.guid,
                    block_id=block.block_id,
                    block_name=block.name,
                    channel=channel,
                    keys=keys,
                    mode=str(spec.get("mode", "hold")),
                    source="manual",
                    local_index=block.local_index,
                    semantic_member_kind=spec.get("semantic_member_kind") or None,
                    keylist_index=int(float(str(manual_keylist_index))) if manual_keylist_index not in (None, "") else -1,
                )
            )
    return channels


def infer_channels(
    blocks: Iterable[BlockInstance],
    manual_path: str | Path | None = None,
    catalog_path: str | Path | None = None,
) -> list[ControlChannel]:
    """Resolve every control channel for the given blocks.

    A BSG's ``<Data>`` fields are never scanned or pattern-matched for
    control-like values: that was a guessing heuristic and is exactly the
    kind of silent, fragile inference this pipeline does not do. The only
    two sources are:

    1. ``manual_path`` — an explicit CSV/JSON map, typically the
       ``control_channels.csv``/``.json`` the controller MOD exports at
       runtime from a loaded machine. Most specific and always wins.
    2. The generated block channel catalog (``catalog_path``), which is
       built from ground-truth game data (Assembly-CSharp reflection +
       in-game live KeyList observation). A block id present in a BSG but
       absent from the catalog raises; a catalog channel that resolved
       neither a semantic name nor a native key also raises. This makes an
       incomplete game-derived catalog explicit instead of silently omitting
       controls.
    """
    block_list = list(blocks)
    manual = _manual_channels(block_list, manual_path)

    channels: list[ControlChannel] = list(manual)
    existing = {channel.channel_id for channel in channels}

    catalog = load_block_channel_catalog(catalog_path) if catalog_path is not None else {}
    for block in block_list:
        entry = catalog.get(block.block_id)
        if entry is None:
            raise ValueError(
                f"Block id {block.block_id!r} ({block.name}, guid={block.guid}) is absent from "
                f"the local block channel catalog {Path(catalog_path) if catalog_path is not None else '<disabled>'}. "
                "Regenerate the catalog from the installed Besiege version."
            )
        for channel_spec in entry.channels:
            if channel_spec.semantic_name is None and not channel_spec.native_keys:
                raise ValueError(
                    f"Channel index {channel_spec.channel_index} for block id {block.block_id!r} "
                    f"({block.name}, guid={block.guid}) has neither a semantic member nor a native key. "
                    "Open a machine containing this block with ColliderDumper enabled, then regenerate "
                    "the channel catalog with scripts/setup.py."
                )
            channel_name = channel_spec.semantic_name or f"channel_{channel_spec.channel_index}"
            channel_id = f"{block.guid}:{channel_name}"
            if channel_id in existing:
                continue
            channels.append(
                ControlChannel(
                    channel_id=channel_id,
                    block_guid=block.guid,
                    block_id=block.block_id,
                    block_name=block.name,
                    channel=channel_name,
                    keys=channel_spec.native_keys,
                    mode="hold",
                    source="catalog" if channel_spec.semantic_name else "catalog_native_only",
                    local_index=block.local_index,
                    semantic_member_kind=channel_spec.semantic_member_kind,
                    keylist_index=channel_spec.channel_index,
                    aliases=channel_spec.aliases,
                )
            )
    return channels


@dataclass(frozen=True)
class InferredSlider:
    block_guid: str
    block_id: str
    block_name: str
    local_index: int
    name: str
    minimum: float | None
    maximum: float | None
    default: float | None


def infer_sliders(
    blocks: Iterable[BlockInstance],
    catalog_path: str | Path | None = None,
) -> list[InferredSlider]:
    """Offline mapper sliders from the catalog, using the same block names as inspect."""
    if catalog_path is None:
        return []
    catalog = load_block_channel_catalog(catalog_path)
    sliders: list[InferredSlider] = []
    for block in blocks:
        entry = catalog.get(block.block_id)
        if entry is None:
            continue
        for slider in entry.sliders:
            sliders.append(
                InferredSlider(
                    block_guid=block.guid,
                    block_id=block.block_id,
                    block_name=block.name,
                    local_index=block.local_index,
                    name=slider.name,
                    minimum=slider.minimum,
                    maximum=slider.maximum,
                    default=slider.default,
                )
            )
    return sliders


def write_mapping(path: str | Path, channels: Iterable[ControlChannel]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "buildarena.key_channel_map.v1",
        "channels": [asdict(channel) for channel in channels],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def channel_detail_lines(block_channels: Iterable[ControlChannel]) -> list[str]:
    """Verbose per-channel protocol details for ``inspect-machine``."""
    lines: list[str] = []
    for channel in block_channels:
        if channel.keylist_index >= 0:
            position = f"KeyList[{channel.keylist_index}]"
        else:
            position = "KeyList position unknown"
        if channel.semantic_member_kind:
            activation = f"direct semantic {channel.semantic_member_kind}"
        elif channel.keylist_index >= 0:
            activation = f"direct KeyList[{channel.keylist_index}]"
        else:
            activation = "UNACTIVATABLE (no semantic member or KeyList index)"
        if channel.source == "catalog_native_only":
            lines.append(
                f'    verbose: anonymous entry at {position}, addressable as channel '
                f'"keylist_{channel.keylist_index}" (function not established); '
                f"activation={activation}"
            )
        else:
            lines.append(
                f'    verbose: "{channel.channel}" at {position}, activation={activation}'
            )
    return lines


def _machine_data(root: ET.Element) -> ET.Element:
    data = root.find("Data")
    if data is None:
        data = ET.Element("Data")
        blocks = root.find("Blocks")
        if blocks is None:
            root.append(data)
        else:
            root.insert(list(root).index(blocks), data)
    return data


def _set_data_value(data: ET.Element, tag: str, key: str, value: str) -> None:
    for child in data:
        if child.attrib.get("key") == key:
            child.tag = tag
            child.text = value
            return
    child = ET.SubElement(data, tag)
    child.set("key", key)
    child.text = value


def _append_required_mod(data: ET.Element, entry: str) -> None:
    required = None
    for child in data:
        if child.tag == "StringArray" and child.attrib.get("key") == "requiredMods":
            required = child
            break
    if required is None:
        required = ET.SubElement(data, "StringArray")
        required.set("key", "requiredMods")
    existing = required.text or ""
    entries = [item.strip() for item in re.split(r"[\r\n]+", existing) if item.strip()]
    if entry not in entries:
        entries.append(entry)
    required.text = "\n".join(entries)


def prepare_machine_bsg(
    source_bsg: str | Path,
    output_bsg: str | Path,
    *,
    run_id: str,
    recorder_enabled: bool = True,
    recorder_hz: float = TELEMETRY_SAMPLE_RATE_HZ,
) -> Path:
    """Write the temporary prepared copy of ``source_bsg`` used for one run.

    The prepared copy gets: a guid for any block missing one, the
    BuildArenaToolKit requiredMods declaration, ``controller.run_id``, and
    (unless recorder_enabled is false) TelemetryRecorder keys bound to
    this run. Recorder targets default to every machine block (``*``);
    access width is the declared profile (default ``full``). The source
    file is never touched.
    """
    if not run_id:
        raise ValueError("prepare_machine_bsg requires a non-empty run_id.")
    if isinstance(recorder_hz, bool) or not isinstance(recorder_hz, (int, float)):
        raise TypeError("recorder_hz must be numeric.")
    frequency = float(recorder_hz)
    if frequency not in (10.0, 25.0, 50.0, 100.0):
        raise ValueError("recorder_hz must be one of 10, 25, 50, or 100.")
    output_path = Path(output_bsg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree, _, _ = parse_bsg(source_bsg, catalog_path=None)
    root = tree.getroot()
    for block in root.findall("./Blocks/Block"):
        if not block.attrib.get("guid"):
            block.set("guid", str(uuid.uuid4()))
    data = _machine_data(root)
    _append_required_mod(data, TOOLKIT_MOD_ENTRY)
    _set_data_value(data, "String", RUN_ID_MACHINE_DATA_KEY, run_id)
    source_sha256 = hashlib.sha256(Path(source_bsg).read_bytes()).hexdigest()
    if recorder_enabled:
        _set_data_value(data, "Boolean", TELEMETRY_ENABLED_KEY, "True")
        _set_data_value(data, "Boolean", TELEMETRY_MANAGED_KEY, "True")
        _set_data_value(data, "Single", TELEMETRY_SAMPLE_RATE_KEY, str(frequency))
        _set_data_value(data, "String", TELEMETRY_TARGET_GUIDS_KEY, TELEMETRY_ALL_TARGETS_TOKEN)
        _set_data_value(data, "String", TELEMETRY_OUTPUT_BASENAME_KEY, Path(source_bsg).stem)
        _set_data_value(data, "String", TELEMETRY_PROFILE_KEY, DEFAULT_TELEMETRY_PROFILE)
        _set_data_value(data, "String", TELEMETRY_SOURCE_SHA256_KEY, source_sha256)
        _set_data_value(data, "String", TELEMETRY_RUN_ID_KEY, run_id)
        ET.indent(tree, space="    ")
        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        machine_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        _set_data_value(data, "String", TELEMETRY_MACHINE_SHA256_KEY, machine_sha256)
    ET.indent(tree, space="    ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def configure_telemetry_bsg(
    source_bsg: str | Path,
    *,
    target_guids: tuple[str, ...] | list[str] | None = None,
    output_basename: str,
    output_bsg: str | Path | None = None,
) -> dict[str, Any]:
    """Write the ToolKit telemetry-recorder configuration into a .bsg.

    Deliberately works on *any* BSG, hand-built machines included: telemetry
    recording needs no MCP build history, only block guids. Writes the four
    ``telemetry.*`` machine-data keys (rate fixed at the protocol's 25 Hz,
    profile default ``full``) plus the ToolKit requiredMods entry.
    An empty ``target_guids`` writes ``*`` (every simulation block).
    In-place (the default) writes a ``<name>.bsg.bak`` backup first;
    ``output_bsg`` writes elsewhere and leaves the source untouched.

    Blocks lacking a guid are assigned a fresh ``uuid4`` (same behavior as
    ``prepare_machine_bsg``) and reported in the returned dict; every
    requested target guid must exist in the BSG or this raises listing the
    machine's actual blocks.
    """
    if not output_basename:
        raise ValueError("configure_telemetry_bsg requires a non-empty output_basename.")
    selected = normalize_telemetry_target_guids(target_guids)
    source_path = Path(source_bsg)
    tree, blocks, _ = parse_bsg(source_path, catalog_path=None)
    root = tree.getroot()

    known_guids = {block.guid for block in blocks if block.guid}
    missing = [guid for guid in selected if guid not in known_guids]
    if missing:
        available = "\n".join(
            f"  {block.local_index:03d} id={block.block_id} guid={block.guid or '<missing>'}"
            for block in blocks
        )
        raise ValueError(
            f"Target guid(s) not present in {source_path}: {missing}. "
            f"The machine's blocks are:\n{available}\n"
            "Run `configure-telemetry --bsg <file>` without --target-guids to list blocks."
        )

    assigned_guids: list[str] = []
    for block in root.findall("./Blocks/Block"):
        if not block.attrib.get("guid"):
            new_guid = str(uuid.uuid4())
            block.set("guid", new_guid)
            assigned_guids.append(new_guid)

    data = _machine_data(root)
    _append_required_mod(data, TOOLKIT_MOD_ENTRY)
    _set_data_value(data, "Boolean", TELEMETRY_ENABLED_KEY, "True")
    _set_data_value(data, "Single", TELEMETRY_SAMPLE_RATE_KEY, str(TELEMETRY_SAMPLE_RATE_HZ))
    written_targets = (
        TELEMETRY_ALL_TARGETS_TOKEN if not selected else ";".join(selected)
    )
    _set_data_value(data, "String", TELEMETRY_TARGET_GUIDS_KEY, written_targets)
    _set_data_value(data, "String", TELEMETRY_OUTPUT_BASENAME_KEY, output_basename)
    _set_data_value(data, "String", TELEMETRY_PROFILE_KEY, DEFAULT_TELEMETRY_PROFILE)

    backup_path: Path | None = None
    if output_bsg is not None:
        written_path = Path(output_bsg)
        written_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        backup_path = source_path.with_suffix(source_path.suffix + ".bak")
        if backup_path.exists():
            raise ValueError(
                f"Backup target already exists: {backup_path}. Move it away before re-running "
                "(it may hold the only pre-configuration copy)."
            )
        shutil.copy2(source_path, backup_path)
        written_path = source_path
    tree.write(written_path, encoding="utf-8", xml_declaration=True)

    return {
        "source": str(source_path),
        "written_to": str(written_path),
        "backup": str(backup_path) if backup_path else "",
        "target_guids": list(selected),
        "targets": "all" if not selected else "subset",
        "sample_rate_hz": TELEMETRY_SAMPLE_RATE_HZ,
        "output_basename": output_basename,
        "profile": DEFAULT_TELEMETRY_PROFILE,
        "assigned_guids": assigned_guids,
        "toolkit_entry": TOOLKIT_MOD_ENTRY,
    }
