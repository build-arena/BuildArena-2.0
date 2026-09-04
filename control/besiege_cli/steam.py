"""Steam install, Workshop, and DLC filesystem probes.

These helpers parse Steam VDF/ACF files and never invent a successful
subscription or DLC state. Workshop production mode requires an explicit
PublishedFileId; an empty ID is reported as unpublished, not as installed.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from controller_sdk.protocol import (
    BESIEGE_DLC_APP_IDS,
    TOOLKIT_MOD_NAME,
    TOOLKIT_WORKSHOP_ITEM_ID,
)

from .paths import BESIEGE_STEAM_APP_ID

BESIEGE_APP_ID = str(BESIEGE_STEAM_APP_ID)


class SteamParseError(ValueError):
    """A Steam VDF/ACF file could not be parsed."""


def parse_vdf(text: str) -> dict[str, Any]:
    """Parse a Steam-style VDF/ACF document into nested dictionaries."""
    tokens = _tokenize_vdf(text)
    position = 0

    def parse_value() -> Any:
        nonlocal position
        if position >= len(tokens):
            raise SteamParseError("Unexpected end of VDF while reading a value.")
        token = tokens[position]
        if token == "{":
            position += 1
            obj: dict[str, Any] = {}
            while position < len(tokens) and tokens[position] != "}":
                key = tokens[position]
                if key in {"{", "}"}:
                    raise SteamParseError(f"Expected a VDF key, got {key!r}.")
                position += 1
                value = parse_value()
                if key in obj:
                    raise SteamParseError(f"Duplicate VDF key {key!r}.")
                obj[key] = value
            if position >= len(tokens) or tokens[position] != "}":
                raise SteamParseError("Unclosed VDF object.")
            position += 1
            return obj
        if token == "}":
            raise SteamParseError("Unexpected '}' while reading a VDF value.")
        position += 1
        return token

    result: dict[str, Any] = {}
    while position < len(tokens):
        key = tokens[position]
        if key in {"{", "}"}:
            raise SteamParseError(f"Expected a top-level VDF key, got {key!r}.")
        position += 1
        if key in result:
            raise SteamParseError(f"Duplicate top-level VDF key {key!r}.")
        result[key] = parse_value()
    return result


def _tokenize_vdf(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if char in "{}":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            index += 1
            current: list[str] = []
            while index < length:
                piece = text[index]
                if piece == "\\" and index + 1 < length:
                    current.append(text[index + 1])
                    index += 2
                    continue
                if piece == '"':
                    index += 1
                    break
                current.append(piece)
                index += 1
            else:
                raise SteamParseError("Unclosed quoted VDF string.")
            tokens.append("".join(current))
            continue
        match = re.match(r"[^\s{}]+", text[index:])
        if match is None:
            raise SteamParseError(f"Invalid VDF token at index {index}.")
        tokens.append(match.group(0))
        index += match.end()
    return tokens


def steam_roots_from_registry() -> list[Path]:
    import winreg

    roots: list[Path] = []
    probes = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for hive, subkey, value_name in probes:
        try:
            handle = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        with handle:
            value, _kind = winreg.QueryValueEx(handle, value_name)
        candidate = Path(str(value))
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def candidate_steam_roots() -> list[Path]:
    roots: list[Path] = list(steam_roots_from_registry())
    for default_root in (
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ):
        if default_root.is_dir():
            roots.append(default_root)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        marker = str(root).lower()
        if marker not in seen:
            seen.add(marker)
            unique.append(root)
    return unique


def library_paths_from_steam_root(*, steam_root: Path) -> list[Path]:
    libraries: list[Path] = [steam_root]
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf_path.is_file():
        return libraries
    parsed = parse_vdf(vdf_path.read_text(encoding="utf-8"))
    folders = parsed.get("libraryfolders")
    if not isinstance(folders, dict):
        raise SteamParseError(
            f"{vdf_path} parsed but has no libraryfolders table; cannot locate extra Steam libraries."
        )
    for entry in folders.values():
        if isinstance(entry, str) and (":" in entry or "\\" in entry or "/" in entry):
            libraries.append(Path(entry.replace("\\\\", "\\")))
            continue
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            libraries.append(Path(raw_path.replace("\\\\", "\\")))
    return libraries


def is_besiege_data(*, path: Path) -> bool:
    return path.is_dir() and (path / "Skins").is_dir()


def detect_besiege_data(*, steam_roots: list[Path] | None = None) -> Path | None:
    roots = candidate_steam_roots() if steam_roots is None else steam_roots
    for steam_root in roots:
        for library in library_paths_from_steam_root(steam_root=steam_root):
            besiege_data = library / "steamapps" / "common" / "Besiege" / "Besiege_Data"
            if is_besiege_data(path=besiege_data):
                return besiege_data
    return None


def normalize_besiege_data(*, raw: str | Path) -> Path:
    candidate = Path(str(raw).strip().strip('"'))
    if is_besiege_data(path=candidate):
        return candidate
    nested = candidate / "Besiege_Data"
    if is_besiege_data(path=nested):
        return nested
    raise FileNotFoundError(
        f"Not a valid Besiege_Data folder (no Skins directory inside): {candidate}"
    )


def steamapps_dir_for_besiege(*, besiege_data: Path) -> Path | None:
    """Return the steamapps directory that owns this Besiege install, if any."""
    current = besiege_data.resolve()
    for parent in current.parents:
        if parent.name.lower() == "steamapps":
            return parent
        common = parent / "steamapps"
        if (common / f"appmanifest_{BESIEGE_APP_ID}.acf").is_file():
            return common
    return None


@dataclass(frozen=True)
class DlcManifestStatus:
    app_id: str
    name: str
    category: str
    manifest_present: bool
    manifest_path: str | None


def dlc_manifest_status(*, besiege_data: Path) -> list[DlcManifestStatus]:
    steamapps = steamapps_dir_for_besiege(besiege_data=besiege_data)
    statuses: list[DlcManifestStatus] = []
    for app_id, name, category in BESIEGE_DLC_APP_IDS:
        manifest = steamapps / f"appmanifest_{app_id}.acf" if steamapps is not None else None
        present = manifest.is_file() if manifest is not None else False
        statuses.append(
            DlcManifestStatus(
                app_id=app_id,
                name=name,
                category=category,
                manifest_present=present,
                manifest_path=str(manifest) if manifest is not None else None,
            )
        )
    return statuses


@dataclass(frozen=True)
class WorkshopStatus:
    item_id: str
    configured: bool
    subscribed: bool
    installed: bool
    content_dir: Path | None
    workshop_url: str
    reason: str


def toolkit_workshop_url(*, item_id: str | None = None) -> str:
    resolved = TOOLKIT_WORKSHOP_ITEM_ID if item_id is None else item_id
    if resolved == "":
        return "https://steamcommunity.com/app/346010/workshop/  # TODO: publish BuildArena ToolKit"
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={resolved}"


def workshop_item_status(
    *,
    besiege_data: Path,
    item_id: str | None = None,
) -> WorkshopStatus:
    resolved = TOOLKIT_WORKSHOP_ITEM_ID if item_id is None else item_id
    url = toolkit_workshop_url(item_id=resolved)
    if resolved == "":
        return WorkshopStatus(
            item_id="",
            configured=False,
            subscribed=False,
            installed=False,
            content_dir=None,
            workshop_url=url,
            reason="TOOLKIT_WORKSHOP_ITEM_ID is empty; the public Workshop item is not configured.",
        )
    steamapps = steamapps_dir_for_besiege(besiege_data=besiege_data)
    if steamapps is None:
        return WorkshopStatus(
            item_id=resolved,
            configured=True,
            subscribed=False,
            installed=False,
            content_dir=None,
            workshop_url=url,
            reason=f"Could not locate steamapps for {besiege_data}.",
        )
    acf_path = steamapps / f"appworkshop_{BESIEGE_APP_ID}.acf"
    subscribed = False
    installed_acf = False
    if acf_path.is_file():
        parsed = parse_vdf(acf_path.read_text(encoding="utf-8"))
        workshop = parsed.get("AppWorkshop", parsed)
        details = workshop.get("WorkshopItemDetails", {}) if isinstance(workshop, dict) else {}
        installed = workshop.get("WorkshopItemsInstalled", {}) if isinstance(workshop, dict) else {}
        subscribed = isinstance(details, dict) and resolved in details
        installed_acf = isinstance(installed, dict) and resolved in installed
    content_dir = steamapps / "workshop" / "content" / BESIEGE_APP_ID / resolved
    content_present = content_dir.is_dir() and any(content_dir.rglob("Mod.xml"))
    if content_present:
        listed = subscribed or installed_acf
        return WorkshopStatus(
            item_id=resolved,
            configured=True,
            subscribed=True,
            installed=True,
            content_dir=content_dir,
            workshop_url=url,
            reason=(
                f"{TOOLKIT_MOD_NAME} Workshop item {resolved} is downloaded at {content_dir}"
                + ("" if listed else f" ({acf_path.name} is missing or does not list it yet)")
            ),
        )
    if subscribed or installed_acf:
        return WorkshopStatus(
            item_id=resolved,
            configured=True,
            subscribed=True,
            installed=False,
            content_dir=content_dir if content_dir.is_dir() else None,
            workshop_url=url,
            reason=(
                f"Workshop item {resolved} is listed in {acf_path.name} but the "
                f"downloaded Mod.xml is not at {content_dir}."
            ),
        )
    return WorkshopStatus(
        item_id=resolved,
        configured=True,
        subscribed=False,
        installed=False,
        content_dir=None,
        workshop_url=url,
        reason=f"Workshop item {resolved} is not subscribed or not listed in {acf_path}.",
    )


def open_workshop_page(*, item_id: str | None = None) -> str:
    url = toolkit_workshop_url(item_id=item_id)
    resolved = TOOLKIT_WORKSHOP_ITEM_ID if item_id is None else item_id
    if resolved == "":
        return url
    steam_uri = f"steam://url/CommunityFilePage/{resolved}"
    subprocess.Popen(
        ["cmd", "/c", "start", "", steam_uri],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return url
