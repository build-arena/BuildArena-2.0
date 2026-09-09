"""Screen recording of the Besiege window via the bundled imageio-ffmpeg binary.

Capture pipeline: gdigrab grabs the Besiege window on Windows (x11grab grabs
the X display named by DISPLAY on Linux), ffmpeg scales/pads to the target
resolution and encodes H.264 at a capped bitrate. The recording is written to
a .part.mkv first (Matroska stays playable after a hard process kill, unlike a
non-finalized mp4) and remuxed to the final .mp4 on stop.

The recorder is split across processes: start_recording() may be called by
one CLI invocation (start-sim) and stop_recording() by another (stop-sim),
so the ffmpeg pid and file paths are persisted in recording_state.json in
the mod data directory.
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe

STATE_FILE_NAME = "recording_state.json"


def recording_state_path(data_dir: Path) -> Path:
    return data_dir / STATE_FILE_NAME


def is_recording(data_dir: Path) -> bool:
    return recording_state_path(data_dir).exists()


def restore_capture_window(window_title: str, *, timeout: float = 3.0) -> bool:
    """Restore an exact-title Windows capture target before gdigrab starts.

    Returns whether a matching window was found. A missing window remains an
    explicit ffmpeg startup error; recording is never silently disabled.
    """
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.ShowWindowAsync.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.ShowWindowAsync.restype = ctypes.c_bool
    user32.GetClientRect.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    user32.GetClientRect.restype = ctypes.c_bool

    handle = user32.FindWindowW(None, window_title)
    if not handle:
        return False
    user32.ShowWindowAsync(handle, 9)  # SW_RESTORE

    class Rect(ctypes.Structure):
        _fields_ = (
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rect = Rect()
        if user32.GetClientRect(handle, ctypes.byref(rect)):
            if rect.right > rect.left and rect.bottom > rect.top:
                return True
        time.sleep(0.05)
    raise RuntimeError(
        f"Window {window_title!r} was found but did not restore to a capturable size "
        f"within {timeout}s."
    )


def _capture_input_args(fps: int, window_title: str) -> list[str]:
    """ffmpeg input for the current platform.

    Windows: gdigrab by exact window title. Linux: x11grab of the whole X
    screen (root window) named by DISPLAY; in a headless X server sized to the
    game resolution that is exactly the game window, a larger screen only adds
    borders. A missing DISPLAY is an explicit error rather than a silent black
    recording.
    """
    if os.name == "nt":
        return ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "0", "-i", f"title={window_title}"]
    display = os.environ.get("DISPLAY", "")
    if not display:
        raise RuntimeError("DISPLAY is not set; x11grab needs the X display the game window is on.")
    return ["-f", "x11grab", "-framerate", str(fps), "-draw_mouse", "0", "-i", display]


def _detach_kwargs() -> dict:
    """Keep ffmpeg alive after the CLI process that started it exits.

    start-sim and stop-sim are separate invocations; on POSIX a new session
    also keeps a terminal Ctrl-C from reaching the capture.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def start_recording(
    *,
    data_dir: Path,
    output: Path,
    fps: int = 25,
    width: int = 1920,
    height: int = 1080,
    video_bitrate_kbps: int = 3500,
    window_title: str = "Besiege",
) -> int:
    """Start a detached ffmpeg capture of the Besiege window; returns its pid."""
    state_path = recording_state_path(data_dir)
    if state_path.exists():
        raise RuntimeError(
            f"A recording is already active ({state_path}). Stop it first (stop-sim or stop_recording)."
        )
    restore_capture_window(window_title)

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    part_file = output.with_suffix(".part.mkv")
    log_file = output.with_suffix(".ffmpeg.log")

    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    command = [
        get_ffmpeg_exe(),
        "-y",
        *_capture_input_args(fps, window_title),
        "-vf", scale_pad,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-b:v", f"{video_bitrate_kbps}k",
        "-maxrate", f"{int(video_bitrate_kbps * 1.3)}k",
        "-bufsize", f"{video_bitrate_kbps * 2}k",
        "-pix_fmt", "yuv420p",
        str(part_file),
    ]
    with log_file.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **_detach_kwargs(),
        )

    # ffmpeg exits within ~1s if the window title does not match, the display
    # cannot be opened, or the grab device is unavailable; surface that instead
    # of recording nothing.
    time.sleep(1.5)
    if process.poll() is not None:
        log_tail = log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise RuntimeError(
            f"ffmpeg exited immediately (code {process.returncode}); capture source "
            f"(window {window_title!r} / DISPLAY {os.environ.get('DISPLAY', '')!r}) probably "
            f"not found.\n--- ffmpeg log tail ---\n{log_tail}"
        )

    state_path.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "part_file": str(part_file),
                "output": str(output),
                "log_file": str(log_file),
                "started_at": time.time(),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return process.pid


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` exists. Reaps it first if it is our own exited child, so
    a zombie ffmpeg (start and stop in the same CLI process) does not count as
    alive. A pid we may not signal belongs to another user: it exists, it is
    not ours to touch."""
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_capture_process(pid: int, part_file: Path) -> bool:
    """Guard against pid reuse after a crash and against zombies left by a
    non-reaping parent: only signal a process whose command line still is our
    ffmpeg x11grab capture writing ``part_file`` (a zombie has an empty command
    line). Linux reads /proc; other POSIX hosts skip the check."""
    cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        argv = cmdline.read_bytes().split(b"\0")
    except OSError:
        return not cmdline.parent.exists()
    return (
        any(b"ffmpeg" in part for part in argv[:1])
        and b"x11grab" in argv
        and str(part_file).encode() in argv
    )


def _signal(pid: int, signum: int) -> bool:
    try:
        os.kill(pid, signum)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate_capture(pid: int, part_file: Path, *, timeout: float = 10.0) -> None:
    """Stop the ffmpeg capture started by an earlier (possibly different) CLI process.

    Windows: hard kill is the only reliable cross-process stop for console
    ffmpeg; the mkv container tolerates it (that is why we record to mkv).
    POSIX: SIGINT lets ffmpeg write the container index, then SIGKILL if it
    has not exited within ``timeout`` seconds. Every wait re-checks that the
    pid is still our capture, so a reused pid is never signalled and a zombie
    (parent that does not reap) never blocks the stop.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        time.sleep(0.5)
        return

    def ours() -> bool:
        return _is_capture_process(pid, part_file) and _pid_alive(pid)

    if not ours() or not _signal(pid, signal.SIGINT):
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and ours():
        time.sleep(0.1)
    if not ours() or not _signal(pid, signal.SIGKILL):
        return
    # SIGKILL has landed; nothing more gets written. Give the kernel a moment
    # to tear the process down, then move on to the remux either way.
    for _ in range(20):
        if not ours():
            return
        time.sleep(0.1)


def stop_recording(*, data_dir: Path) -> Path:
    """Stop the active capture, remux .part.mkv to the final .mp4, return its path."""
    state_path = recording_state_path(data_dir)
    if not state_path.exists():
        raise RuntimeError(f"No active recording ({state_path} does not exist).")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    part_file = Path(state["part_file"])
    output = Path(state["output"])

    _terminate_capture(int(state["pid"]), part_file)

    if not part_file.exists():
        state_path.unlink()
        raise RuntimeError(f"Recording part file missing: {part_file}. Nothing to remux.")

    remux = subprocess.run(
        [get_ffmpeg_exe(), "-y", "-i", str(part_file), "-c", "copy", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    if remux.returncode != 0:
        raise RuntimeError(f"Remux to {output} failed:\n{remux.stderr[-2000:]}")

    part_file.unlink()
    log_file = Path(state["log_file"])
    if log_file.exists():
        log_file.unlink()
    state_path.unlink()
    return output
