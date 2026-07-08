"""
Inject BuildArena Block Tracker config into a hand-authored Besiege `.bsg`.

Usage:
    uv run python scripts/inject_starting_block_tracker.py --bsg ".local/Machine/02-2.bsg"
    uv run python scripts/inject_starting_block_tracker.py --bsg ".local/Machine/02-2.bsg" --output ".local/Machine/02-2_tracked.bsg"
"""

from __future__ import annotations

import argparse
import html
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buildarena.paths import resolve_project_path


BLOCK_TRACKER_MOD_ID = "1d45bae7-50b4-4137-963e-27e45f6ece74"
BLOCK_TRACKER_MOD_VERSION = "1.0.0"
BLOCK_TRACKER_MOD_NAME = "BuildArena Block Tracker"
TRACKER_KEYS = {
    "tracker.enabled",
    "tracker.sample_rate_hz",
    "tracker.target_guids",
    "tracker.output_basename",
}


@dataclass(frozen=True, kw_only=True)
class InjectResult:
    input_path: Path
    output_path: Path
    starting_block_guid: str
    output_basename: str


def _required_mod_entry() -> str:
    return f"{BLOCK_TRACKER_MOD_ID}~L~{BLOCK_TRACKER_MOD_VERSION}~{BLOCK_TRACKER_MOD_NAME}"


def _text_element(*, tag: str, key: str, value: str) -> ET.Element:
    escaped_key = html.escape(s=key, quote=True)
    escaped_value = html.escape(s=value, quote=False)
    return ET.fromstring(text=f'<{tag} key="{escaped_key}">{escaped_value}</{tag}>')


def _parse_bsg(*, bsg_path: Path) -> ET.ElementTree:
    if not bsg_path.is_file():
        raise FileNotFoundError(f"BSG file not found: {bsg_path}")

    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(source=bsg_path, parser=parser)


def _machine_name(*, root: ET.Element) -> str:
    name = root.attrib.get("name")
    if name is None or name.strip() == "":
        raise ValueError("Machine root is missing a non-empty name attribute.")
    return name


def _machine_data_node(*, root: ET.Element) -> ET.Element:
    data_nodes = root.findall(path="Data")
    if len(data_nodes) != 1:
        raise ValueError(f"Expected exactly one machine-level Data node, found {len(data_nodes)}.")
    return data_nodes[0]


def _starting_block_guid(*, root: ET.Element) -> str:
    starting_blocks = root.findall(path="./Blocks/Block[@id='0']")
    if len(starting_blocks) != 1:
        raise ValueError(f"Expected exactly one Starting Block with id='0', found {len(starting_blocks)}.")

    guid = starting_blocks[0].attrib.get("guid")
    if guid is None or guid.strip() == "":
        raise ValueError("Starting Block is missing a non-empty guid attribute.")
    return guid


def _required_mods_node(*, data_node: ET.Element) -> ET.Element:
    required_mods_nodes = [
        child
        for child in list(data_node)
        if child.tag == "StringArray" and child.attrib.get("key") == "requiredMods"
    ]
    if len(required_mods_nodes) > 1:
        raise ValueError(f"Expected at most one requiredMods node, found {len(required_mods_nodes)}.")
    if len(required_mods_nodes) == 1:
        return required_mods_nodes[0]

    required_mods_node = _text_element(tag="StringArray", key="requiredMods", value="")
    data_node[:] = [*list(data_node), required_mods_node]
    return required_mods_node


def _inject_required_mod(*, data_node: ET.Element) -> None:
    required_mods_node = _required_mods_node(data_node=data_node)
    required_mod_entry = _required_mod_entry()
    existing_required_mods = (required_mods_node.text or "").strip()

    if existing_required_mods == "" or existing_required_mods == required_mod_entry:
        required_mods_node.text = required_mod_entry
        return
    if required_mod_entry in existing_required_mods:
        return

    raise ValueError(
        "requiredMods already contains other entries; merge format is ambiguous. "
        f"Existing value: {existing_required_mods}"
    )


def _remove_existing_tracker_config(*, data_node: ET.Element) -> None:
    data_node[:] = [
        child
        for child in list(data_node)
        if child.attrib.get("key") not in TRACKER_KEYS
    ]


def _append_tracker_config(
    *,
    data_node: ET.Element,
    sample_rate_hz: float,
    starting_block_guid: str,
    output_basename: str,
) -> None:
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be greater than zero.")

    tracker_nodes = [
        _text_element(tag="Boolean", key="tracker.enabled", value="True"),
        _text_element(tag="Single", key="tracker.sample_rate_hz", value=str(float(sample_rate_hz))),
        _text_element(tag="String", key="tracker.target_guids", value=starting_block_guid),
        _text_element(tag="String", key="tracker.output_basename", value=output_basename),
    ]
    data_node[:] = [*list(data_node), *tracker_nodes]


def inject_starting_block_tracker(
    *,
    bsg_path: Path,
    output_path: Path | None = None,
    sample_rate_hz: float = 10.0,
    output_basename: str | None = None,
) -> InjectResult:
    resolved_bsg_path = resolve_project_path(path=bsg_path)
    resolved_output_path = (
        resolved_bsg_path
        if output_path is None
        else resolve_project_path(path=output_path)
    )

    tree = _parse_bsg(bsg_path=resolved_bsg_path)
    root = tree.getroot()
    data_node = _machine_data_node(root=root)
    starting_block_guid = _starting_block_guid(root=root)
    resolved_output_basename = _machine_name(root=root) if output_basename is None else output_basename
    if resolved_output_basename.strip() == "":
        raise ValueError("output_basename must be non-empty.")

    _inject_required_mod(data_node=data_node)
    _remove_existing_tracker_config(data_node=data_node)
    _append_tracker_config(
        data_node=data_node,
        sample_rate_hz=sample_rate_hz,
        starting_block_guid=starting_block_guid,
        output_basename=resolved_output_basename,
    )

    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree=tree, space="    ")
    tree.write(file_or_filename=resolved_output_path, encoding="utf-8", xml_declaration=True)

    return InjectResult(
        input_path=resolved_bsg_path,
        output_path=resolved_output_path,
        starting_block_guid=starting_block_guid,
        output_basename=resolved_output_basename,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inject BuildArena Block Tracker config for the Starting Block in a hand-authored .bsg."
    )
    parser.add_argument(
        "--bsg",
        required=True,
        type=Path,
        help="Path to the hand-authored .bsg file. Defaults to in-place write.",
    )
    parser.add_argument(
        "--output",
        default=None,
        type=Path,
        help="Optional output .bsg path. If omitted, the input file is modified in place.",
    )
    parser.add_argument(
        "--sample-rate-hz",
        default=10.0,
        type=float,
        help="Tracker sampling rate. Defaults to 10.0.",
    )
    parser.add_argument(
        "--output-basename",
        default=None,
        help="Optional tracker output basename. Defaults to the Machine name attribute.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    result = inject_starting_block_tracker(
        bsg_path=args.bsg,
        output_path=args.output,
        sample_rate_hz=args.sample_rate_hz,
        output_basename=args.output_basename,
    )
    print(f"input_path={result.input_path}")
    print(f"output_path={result.output_path}")
    print(f"starting_block_guid={result.starting_block_guid}")
    print(f"output_basename={result.output_basename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
