from __future__ import annotations

import os
from pathlib import Path

# Mod identity and the ModIO data folder name come from the single Python
# source of truth in controller_sdk.protocol.
from controller_sdk.protocol import TOOLKIT_DATA_DIR_NAME

# Confirmed via steamapps/appmanifest_346010.acf ("name" "Besiege").
BESIEGE_STEAM_APP_ID = 346010


def datacache_dir() -> Path:
    """Repo-root `datacache/` — the Git-ignored home for all experiment and
    run outputs. Anchored to this file, not the current working directory,
    so runs land in the same place no matter where the CLI is invoked from.
    """
    return Path(__file__).resolve().parents[2] / "datacache"


def resolve_besiege_data(besiege_data: str | Path | None) -> Path:
    """Resolve and validate the Besiege_Data directory.

    Never guesses a default install location: besiege_data must come from
    an explicit --besiege-data argument or the BESIEGE_DATA_PATH
    environment variable, since install locations vary by machine (this
    repository has already seen both an E:\\SteamLibrary\\... and a
    C:\\Program Files (x86)\\Steam\\... install across different sessions).
    """
    if besiege_data is not None:
        path = Path(besiege_data)
    else:
        env_value = os.environ.get("BESIEGE_DATA_PATH")
        if not env_value:
            raise ValueError(
                "Besiege_Data path not provided: pass --besiege-data <path> or set the "
                "BESIEGE_DATA_PATH environment variable (e.g. "
                r"'C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data')."
            )
        path = Path(env_value)
    if not path.is_dir():
        raise FileNotFoundError(f"Besiege_Data directory not found: {path}")
    return path


def besiege_install_root(besiege_data: Path) -> Path:
    root = besiege_data.parent
    exe = root / "Besiege.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Besiege.exe not found next to Besiege_Data: {exe}")
    return root


def besiege_exe(besiege_data: Path) -> Path:
    return besiege_install_root(besiege_data) / "Besiege.exe"


def resolve_channel_catalog(catalog: str | Path | None = None) -> Path:
    """Resolve the channel catalog from an override or the ToolKit data dir."""
    from buildarena.paths import get_block_channel_catalog_path

    return get_block_channel_catalog_path(catalog_path=catalog)


def mod_data_dir(besiege_data: Path) -> Path:
    """Directory the mod reads/writes its file-based protocol from.

    Matches Modding.ModIO's own resolution for `useModFolder=true` (see
    Orchestrator.cs / ControllerRunner.cs), which every artifact
    (control_timeline.json, telemetry_sample.json, orchestrator_state.json,
    ...) already goes through the same way.
    """
    return besiege_data / "Mods" / "Data" / TOOLKIT_DATA_DIR_NAME


def saved_machines_dir(besiege_data: Path) -> Path:
    """Besiege's native saved-machine directory.

    Confirmed empirically (not assumed): vanilla Besiege stores manual
    quicksaves here as flat "<name>.bsg" files directly in this directory
    (e.g. "001.bsg"), not in a folder-per-machine layout. The
    "SavedMachines/BuildArena/<name>/<name>.bsg" layout seen in some of
    this repo's test fixtures is a project-specific convention for
    organizing named test rigs with JSON metadata sidecars; it is not how
    Besiege's own file browser (LocalMachineCollection /
    MachineFileBrowserController) scans this directory, so besiege_cli's
    load-machine command targets this flat layout instead.
    """
    return besiege_data / "SavedMachines"
