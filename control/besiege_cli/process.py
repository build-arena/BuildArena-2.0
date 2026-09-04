from __future__ import annotations

import subprocess
from pathlib import Path

from .paths import BESIEGE_STEAM_APP_ID

BESIEGE_PROCESS_NAME = "Besiege.exe"


def launch_via_steam() -> None:
    """Ask the local Steam client to launch Besiege by its confirmed app id.

    Uses the OS URI handler (via `cmd /c start`) rather than shelling out to
    steam.exe directly, since Steam's own URI protocol handler is what
    actually owns update-checking/DRM before the game process starts.
    """
    subprocess.Popen(
        ["cmd", "/c", "start", "", f"steam://rungameid/{BESIEGE_STEAM_APP_ID}"],
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def launch_via_exe(besiege_root: Path) -> None:
    exe = besiege_root / BESIEGE_PROCESS_NAME
    if not exe.is_file():
        raise FileNotFoundError(f"{BESIEGE_PROCESS_NAME} not found: {exe}")
    subprocess.Popen([str(exe)], cwd=str(besiege_root))


def find_besiege_pids() -> list[int]:
    """Return PIDs of running Besiege.exe processes via tasklist.

    Avoids taking a dependency on psutil for a single-purpose PID lookup;
    tasklist is a stock Windows tool.
    """
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {BESIEGE_PROCESS_NAME}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=True,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("INFO:"):
            continue
        fields = [field.strip('"') for field in line.split('","')]
        fields[0] = fields[0].lstrip('"')
        if fields and fields[0] == BESIEGE_PROCESS_NAME and len(fields) >= 2:
            try:
                pids.append(int(fields[1]))
            except ValueError:
                continue
    return pids


def force_kill(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=True)
