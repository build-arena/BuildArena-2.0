from __future__ import annotations

import os
import platform
import shlex
import subprocess
from pathlib import Path

from .paths import BESIEGE_STEAM_APP_ID, besiege_exe_name

# Optional launch override. When set, this exact command line is run instead
# of the game binary. Headless Linux hosts need it: the game still requires an
# X display (Xvfb) and, on machines with many cores, a wrapper that limits the
# core count Unity sees. Keeping that host-specific knowledge in an env var
# leaves this module portable.
LAUNCH_COMMAND_ENV = "BESIEGE_LAUNCH_COMMAND"


def _default_process_name() -> str:
    try:
        return besiege_exe_name()
    except RuntimeError:
        return "Besiege.exe"


# Kept as a module constant for callers and log messages.
BESIEGE_PROCESS_NAME = _default_process_name()


class UnsupportedPlatformError(RuntimeError):
    """The host OS has no implementation for this process operation."""


def steam_launch_available() -> bool:
    """Whether asking the local Steam client to launch the game can work.

    Headless Linux servers generally have no Steam client, and its login UI
    cannot be driven without a GPU. Callers check this to skip straight to a
    direct binary launch instead of waiting out a launch timeout.
    """
    system = platform.system()
    if system == "Windows":
        return True
    if system == "Linux":
        from shutil import which

        return which("steam") is not None or which("xdg-open") is not None
    return False


def launch_via_steam() -> None:
    """Ask the local Steam client to launch Besiege by its confirmed app id.

    Uses the OS URI handler rather than shelling out to steam.exe directly,
    since Steam's own URI protocol handler is what actually owns
    update-checking/DRM before the game process starts.
    """
    system = platform.system()
    uri = f"steam://rungameid/{BESIEGE_STEAM_APP_ID}"
    if system == "Windows":
        subprocess.Popen(
            ["cmd", "/c", "start", "", uri],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    if system == "Linux":
        from shutil import which

        opener = which("steam") or which("xdg-open")
        if opener is None:
            raise UnsupportedPlatformError(
                "No Steam client or xdg-open on this host; launch the game binary directly."
            )
        argv = [opener, uri] if opener.endswith("xdg-open") else [opener, uri]
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    raise UnsupportedPlatformError(f"Steam launch is not implemented for {system}.")


def launch_via_exe(besiege_root: Path) -> None:
    """Start the game binary directly.

    ``BESIEGE_LAUNCH_COMMAND`` takes precedence when set, so a headless host
    can point at its own wrapper script (virtual display, core limiting, mod
    loader injection) without this module knowing about any of it.
    """
    override = os.environ.get(LAUNCH_COMMAND_ENV, "").strip()
    if override:
        subprocess.Popen(
            shlex.split(override),
            cwd=str(besiege_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return

    exe = besiege_root / besiege_exe_name()
    if not exe.is_file():
        raise FileNotFoundError(f"{exe.name} not found: {exe}")
    if platform.system() == "Windows":
        subprocess.Popen([str(exe)], cwd=str(besiege_root))
        return
    subprocess.Popen(
        [str(exe)],
        cwd=str(besiege_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _find_pids_windows() -> list[int]:
    """Return PIDs of running Besiege processes via tasklist.

    Avoids taking a dependency on psutil for a single-purpose PID lookup;
    tasklist is a stock Windows tool.
    """
    name = besiege_exe_name("Windows")
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
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
        if fields and fields[0] == name and len(fields) >= 2:
            try:
                pids.append(int(fields[1]))
            except ValueError:
                continue
    return pids


def _find_pids_linux() -> list[int]:
    """Return PIDs of this user's running Besiege processes, read from /proc.

    Only this user's processes count. Shared research machines can have other
    people running the same game, and killing someone else's session would be
    a real loss of work.

    ``/proc/<pid>/comm`` is truncated to 15 characters by the kernel, which
    the current binary name fits inside; the prefix comparison keeps a longer
    future name working.
    """
    name = besiege_exe_name("Linux")
    truncated = name[:15]
    uid = os.getuid()
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if comm == name or comm == truncated:
            pids.append(int(entry.name))
    return sorted(pids)


def find_besiege_pids() -> list[int]:
    system = platform.system()
    if system == "Windows":
        return _find_pids_windows()
    if system == "Linux":
        return _find_pids_linux()
    raise UnsupportedPlatformError(f"Process lookup is not implemented for {system}.")


def force_kill(pid: int) -> None:
    system = platform.system()
    if system == "Windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=True)
        return
    if system == "Linux":
        import signal

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        return
    raise UnsupportedPlatformError(f"Force kill is not implemented for {system}.")
