"""Read and enable BuildArena ToolKit in Besiege's Modding.xml."""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from controller_sdk.protocol import TOOLKIT_MOD_ID, TOOLKIT_MOD_NAME

from .paths import output_log_candidates
from .process import find_besiege_pids, force_kill

MODDING_RELATIVE = Path("Mods") / "Config" / "Modding.xml"
CONFIG_RELATIVE = Path("Config.xml")
DISABLED_MODS_KEY = "disabled-mods"
LAST_GAME_VERSION_KEY = "maintenance-lastGameVersion"
BESIEGE_VERSION_RE = re.compile(
    r"^\[Besiege\] version:\s*(?P<version>\d+(?:\.\d+)*-\d+)\b",
    re.MULTILINE,
)
LAST_VERSION_RE = re.compile(r"<LastVersion>[^<]*</LastVersion>")
LAST_GAME_VERSION_RE = re.compile(
    rf'(<String key="{re.escape(LAST_GAME_VERSION_KEY)}">)[^<]*(</String>)'
)
TOOLKIT_VERSION_DISABLE_MARKER = (
    f"[ModLoader] Game version changed detected, turning off {TOOLKIT_MOD_NAME}"
)
PROCESS_EXIT_TIMEOUT_SECONDS = 20.0


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


@dataclass(frozen=True)
class OutputLogInfo:
    path: Path
    installed_version: str | None
    toolkit_disabled_by_version_change: bool


@dataclass(frozen=True)
class VersionAckResult:
    version: str
    previous_last_version: str
    config_path: Path
    config_backup: Path | None
    config_changed: bool
    last_game_version_changed: bool


@dataclass(frozen=True)
class ToolkitReadyResult:
    enabled: ModEnableResult
    acknowledged_version: str | None
    previous_last_version: str | None
    config_changed: bool
    last_game_version_changed: bool
    output_log: Path | None


@dataclass(frozen=True)
class ToolkitRecoveryResult:
    acknowledged_version: str
    previous_last_version: str
    killed_pids: tuple[int, ...]
    enabled: ModEnableResult
    config_changed: bool
    last_game_version_changed: bool
    output_log: Path


def config_xml_path(*, besiege_data: Path) -> Path:
    return besiege_data / CONFIG_RELATIVE


def resolve_output_log(
    *,
    besiege_data: Path,
    system: str | None = None,
    home: Path | None = None,
) -> Path | None:
    """Return the player log for this host, or None if none of the candidates exist.

    When more than one candidate exists, their Besiege version lines must agree.
    Conflicting versions are an error; this does not pick one silently.
    """
    existing = [
        path
        for path in output_log_candidates(besiege_data=besiege_data, system=system, home=home)
        if path.is_file()
    ]
    if not existing:
        return None
    if len(existing) == 1:
        return existing[0]
    infos = [parse_output_log(path=path) for path in existing]
    versions = {info.installed_version for info in infos if info.installed_version is not None}
    if len(versions) > 1:
        listed = ", ".join(f"{info.path} ({info.installed_version})" for info in infos)
        raise ModdingConfigError(
            "Player logs disagree on the installed Besiege version: "
            f"{listed}. Refusing to guess which build is current."
        )
    return max(existing, key=lambda path: path.stat().st_mtime)


def parse_output_log(*, path: Path) -> OutputLogInfo:
    if not path.is_file():
        raise ModdingConfigError(f"{path} does not exist.")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ModdingConfigError(f"{path} is not valid UTF-8: {error}") from error
    matches = list(BESIEGE_VERSION_RE.finditer(text))
    versions = {match.group("version") for match in matches}
    if len(versions) > 1:
        raise ModdingConfigError(
            f"{path} reports more than one Besiege version: {sorted(versions)}. "
            "Refusing to guess which build is installed."
        )
    installed_version = matches[-1].group("version") if matches else None
    return OutputLogInfo(
        path=path,
        installed_version=installed_version,
        toolkit_disabled_by_version_change=TOOLKIT_VERSION_DISABLE_MARKER in text,
    )


def read_config_last_version(*, path: Path) -> str:
    if not path.is_file():
        raise ModdingConfigError(
            f"{path} does not exist. Launch Besiege once so it writes Config.xml."
        )
    matches = LAST_VERSION_RE.findall(path.read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise ModdingConfigError(
            f"{path} must contain exactly one <LastVersion> element; found {len(matches)}."
        )
    start = matches[0].find(">") + 1
    end = matches[0].rfind("<")
    version = matches[0][start:end].strip()
    if not version:
        raise ModdingConfigError(f"{path} has an empty <LastVersion> element.")
    return version


def _replace_once(*, path: Path, pattern: re.Pattern[str], replacement: str) -> bool:
    text = path.read_text(encoding="utf-8")
    updated, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _backup_file(path: Path) -> Path:
    backup_path = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup_path)
    return backup_path


def acknowledge_installed_version(
    *,
    besiege_data: Path,
    version: str,
    allow_while_running: bool = False,
) -> VersionAckResult:
    """Record the installed Besiege version so ModLoader will not disable ToolKit.

    A stale Config.xml LastVersion is what triggers
    ``[GameVersion] Update detected`` and then
    ``turning off BuildArena ToolKit`` on the next launch.
    """
    pids = find_besiege_pids()
    if pids and not allow_while_running:
        raise ModdingConfigError(
            f"Besiege is running (PIDs {pids}); refusing to edit game config "
            "while the process holds those files. Quit Besiege, then re-run setup."
        )
    config_path = config_xml_path(besiege_data=besiege_data)
    previous = read_config_last_version(path=config_path)
    config_changed = previous != version
    last_game_version_changed = False
    backup_path = None
    if config_changed:
        backup_path = _backup_file(config_path)
        replaced = _replace_once(
            path=config_path,
            pattern=LAST_VERSION_RE,
            replacement=f"<LastVersion>{version}</LastVersion>",
        )
        if not replaced:
            raise ModdingConfigError(f"{config_path} lost its <LastVersion> element during rewrite.")
        written = read_config_last_version(path=config_path)
        if written != version:
            raise ModdingConfigError(
                f"Wrote {config_path} but LastVersion is {written!r}, not {version!r}."
            )
    modding_path = modding_xml_path(besiege_data=besiege_data)
    if modding_path.is_file():
        text = modding_path.read_text(encoding="utf-8")
        if LAST_GAME_VERSION_RE.search(text) is not None:
            updated, count = LAST_GAME_VERSION_RE.subn(
                rf"\g<1>{version}\g<2>",
                text,
                count=1,
            )
            if count != 1:
                raise ModdingConfigError(
                    f"{modding_path} has {LAST_GAME_VERSION_KEY} but it could not be updated."
                )
            if updated != text:
                _backup_file(modding_path)
                modding_path.write_text(updated, encoding="utf-8")
                last_game_version_changed = True
    return VersionAckResult(
        version=version,
        previous_last_version=previous,
        config_path=config_path,
        config_backup=backup_path,
        config_changed=config_changed,
        last_game_version_changed=last_game_version_changed,
    )


def ensure_toolkit_ready(
    *,
    besiege_data: Path,
    allow_while_running: bool = False,
    system: str | None = None,
    home: Path | None = None,
) -> ToolkitReadyResult:
    """Enable ToolKit and acknowledge a newer game version already seen in the player log."""
    enabled = enable_toolkit_mod(
        besiege_data=besiege_data,
        allow_while_running=allow_while_running,
    )
    log_path = resolve_output_log(besiege_data=besiege_data, system=system, home=home)
    if not log_path.is_file():
        return ToolkitReadyResult(
            enabled=enabled,
            acknowledged_version=None,
            previous_last_version=None,
            config_changed=False,
            last_game_version_changed=False,
            output_log=None,
        )
    info = parse_output_log(path=log_path)
    if info.installed_version is None:
        return ToolkitReadyResult(
            enabled=enabled,
            acknowledged_version=None,
            previous_last_version=None,
            config_changed=False,
            last_game_version_changed=False,
            output_log=log_path,
        )
    previous = read_config_last_version(path=config_xml_path(besiege_data=besiege_data))
    if previous == info.installed_version:
        return ToolkitReadyResult(
            enabled=enabled,
            acknowledged_version=info.installed_version,
            previous_last_version=previous,
            config_changed=False,
            last_game_version_changed=False,
            output_log=log_path,
        )
    ack = acknowledge_installed_version(
        besiege_data=besiege_data,
        version=info.installed_version,
        allow_while_running=allow_while_running,
    )
    enabled_again = enable_toolkit_mod(
        besiege_data=besiege_data,
        allow_while_running=allow_while_running,
    )
    return ToolkitReadyResult(
        enabled=enabled_again if enabled_again.changed else enabled,
        acknowledged_version=ack.version,
        previous_last_version=ack.previous_last_version,
        config_changed=ack.config_changed,
        last_game_version_changed=ack.last_game_version_changed,
        output_log=log_path,
    )


def _wait_until_besiege_exits(*, timeout: float = PROCESS_EXIT_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = find_besiege_pids()
        if not remaining:
            return
        time.sleep(0.25)
    remaining = find_besiege_pids()
    raise ModdingConfigError(
        f"Besiege PIDs {remaining} are still running after force-kill; "
        "cannot edit Config.xml or Modding.xml."
    )


def recover_toolkit_after_failed_launch(
    *,
    besiege_data: Path,
    system: str | None = None,
    home: Path | None = None,
) -> ToolkitRecoveryResult | None:
    """Re-enable ToolKit after ModLoader turned it off for a game update.

    Returns None when the platform player log does not show that version-change
    disable, so the caller can re-raise the original launch error.
    """
    log_path = resolve_output_log(besiege_data=besiege_data, system=system, home=home)
    if log_path is None:
        return None
    info = parse_output_log(path=log_path)
    if not info.toolkit_disabled_by_version_change:
        return None
    if info.installed_version is None:
        raise ModdingConfigError(
            f"{log_path} shows ModLoader turned off {TOOLKIT_MOD_NAME} after a "
            "game version change, but has no '[Besiege] version:' line. "
            "Cannot acknowledge the new version."
        )
    killed = tuple(find_besiege_pids())
    for pid in killed:
        force_kill(pid)
    _wait_until_besiege_exits()
    ack = acknowledge_installed_version(
        besiege_data=besiege_data,
        version=info.installed_version,
    )
    enabled = enable_toolkit_mod(besiege_data=besiege_data)
    return ToolkitRecoveryResult(
        acknowledged_version=ack.version,
        previous_last_version=ack.previous_last_version,
        killed_pids=killed,
        enabled=enabled,
        config_changed=ack.config_changed,
        last_game_version_changed=ack.last_game_version_changed,
        output_log=log_path,
    )
