"""Read and enable BuildArena ToolKit in Besiege's Modding.xml."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from controller_sdk.protocol import TOOLKIT_MOD_ID, TOOLKIT_MOD_NAME

from .process import find_besiege_pids

MODDING_RELATIVE = Path("Mods") / "Config" / "Modding.xml"
DISABLED_MODS_KEY = "disabled-mods"


class ModdingConfigError(RuntimeError):
    """Besiege Modding.xml could not be read or updated safely."""


@dataclass(frozen=True)
class ModEnableResult:
    path: Path
    backup_path: Path | None
    already_enabled: bool
    changed: bool
    disabled_after: tuple[str, ...]


def modding_xml_path(*, besiege_data: Path) -> Path:
    return besiege_data / MODDING_RELATIVE


def _string_array_values(root: ET.Element, key: str) -> list[str]:
    for node in root.findall("StringArray"):
        if node.attrib.get("key") == key:
            return [child.text.strip() for child in node.findall("String") if child.text]
    return []


def read_disabled_mod_ids(*, path: Path) -> list[str]:
    if not path.is_file():
        raise ModdingConfigError(
            f"{path} does not exist. Launch Besiege once so the mod loader writes Modding.xml."
        )
    tree = ET.parse(path)
    return _string_array_values(tree.getroot(), DISABLED_MODS_KEY)


def toolkit_is_disabled(*, path: Path) -> bool:
    return TOOLKIT_MOD_ID in read_disabled_mod_ids(path=path)


def enable_toolkit_mod(*, besiege_data: Path, allow_while_running: bool = False) -> ModEnableResult:
    """Remove the ToolKit UUID from disabled-mods.

    Refuses to rewrite the file while Besiege is running unless the caller
    explicitly allows it. Writes a ``.bak`` beside the original, then
    replaces the file atomically and re-reads the result.
    """
    path = modding_xml_path(besiege_data=besiege_data)
    if not path.is_file():
        raise ModdingConfigError(
            f"{path} does not exist. Launch Besiege once so the mod loader writes Modding.xml, "
            f"then re-run setup so {TOOLKIT_MOD_NAME} can be enabled."
        )
    disabled = read_disabled_mod_ids(path=path)
    if TOOLKIT_MOD_ID not in disabled:
        return ModEnableResult(
            path=path,
            backup_path=None,
            already_enabled=True,
            changed=False,
            disabled_after=tuple(disabled),
        )
    pids = find_besiege_pids()
    if pids and not allow_while_running:
        raise ModdingConfigError(
            f"Besiege is running (PIDs {pids}); refusing to edit {path} while the game "
            f"holds the mod-loader config. Quit Besiege, then re-run setup."
        )

    tree = ET.parse(path)
    root = tree.getroot()
    target = None
    for node in root.findall("StringArray"):
        if node.attrib.get("key") == DISABLED_MODS_KEY:
            target = node
            break
    if target is None:
        raise ModdingConfigError(
            f"{path} has no <StringArray key=\"{DISABLED_MODS_KEY}\">; refusing to invent one."
        )
    removed = False
    for child in list(target.findall("String")):
        if (child.text or "").strip() == TOOLKIT_MOD_ID:
            target.remove(child)
            removed = True
    if not removed:
        raise ModdingConfigError(
            f"{path} listed {TOOLKIT_MOD_ID} when first read, but the node was gone on rewrite."
        )

    backup_path = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup_path)
    ET.indent(tree, space="    ")
    temporary = path.with_name(path.name + ".tmp")
    tree.write(temporary, encoding="utf-8", xml_declaration=True)
    temporary.replace(path)

    after = read_disabled_mod_ids(path=path)
    if TOOLKIT_MOD_ID in after:
        raise ModdingConfigError(
            f"Wrote {path} but {TOOLKIT_MOD_ID} is still in {DISABLED_MODS_KEY}."
        )
    return ModEnableResult(
        path=path,
        backup_path=backup_path,
        already_enabled=False,
        changed=True,
        disabled_after=tuple(after),
    )
