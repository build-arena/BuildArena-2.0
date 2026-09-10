"""Reusable game-session helpers shared by experiment scripts.

These used to live inside scripts/run_pendulum_pid.py, forcing every new
experiment script to import a pendulum-named module just to launch the game
or enter the sandbox.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .orchestrator import BesiegeOrchestrator, OrchestratorTimeoutError
from .paths import besiege_install_root
from .process import find_besiege_pids, force_kill, launch_via_exe, launch_via_steam

DEFAULT_SANDBOX_LEVEL = "BARREN EXPANSE"


@dataclass(frozen=True)
class GameSession:
    already_running: bool
    launched: bool


def ensure_game(*, orchestrator: BesiegeOrchestrator, besiege_data: Path, timeout: float) -> GameSession:
    """Make sure Besiege is running with a live mod heartbeat, launching it
    (Steam URI first, direct exe as fallback) if it is not.

    Returns whether this call found an already-running session or launched
    a new process. Callers that started the game are responsible for
    deciding whether to quit it.
    """
    mtime = orchestrator.state_mtime()
    if mtime is not None and time.time() - mtime < 5.0 and find_besiege_pids():
        print("Besiege is already running with a live ToolKit heartbeat.", flush=True)
        return GameSession(already_running=True, launched=False)
    before = orchestrator.state_mtime()
    existing = set(find_besiege_pids())
    print(
        "Launching Besiege via Steam. After the window appears, ToolKit still "
        "needs to finish loading — progress will print every 10s.",
        flush=True,
    )
    launch_via_steam()
    try:
        orchestrator.wait_for_heartbeat(timeout=timeout, after_mtime=before)
        return GameSession(already_running=False, launched=True)
    except OrchestratorTimeoutError:
        pass
    new_pids = set(find_besiege_pids()) - existing
    if new_pids:
        raise OrchestratorTimeoutError(
            f"Besiege PIDs {sorted(new_pids)} started but no mod heartbeat within {timeout}s "
            "(mod failed to load? check output_log.txt)."
        )
    print("Steam launch produced no process; launching Besiege.exe directly...")
    launch_via_exe(besiege_install_root(besiege_data))
    orchestrator.wait_for_heartbeat(timeout=timeout, after_mtime=before)
    return GameSession(already_running=False, launched=True)


def ensure_sandbox(
    *,
    orchestrator: BesiegeOrchestrator,
    timeout: float,
    level: str = DEFAULT_SANDBOX_LEVEL,
    force: bool = False,
) -> None:
    """Enter the given sandbox level unless the game is already in it.

    ``force=True`` re-sends ``enter_sandbox`` even when already in ``level``.
    Inspector requests are picked up on scene load, so setup must force a
    load after writing ``inspector_request.json``.
    """
    state = orchestrator.read_state()
    current = state.get("scene")
    if current == level and not force:
        print(f"Already in sandbox {level!r}.", flush=True)
        return
    print(
        f"Entering sandbox {level!r} (current scene={current!r}). "
        "Level load can take a while after Besiege is visible.",
        flush=True,
    )
    sequence = orchestrator.send_command("enter_sandbox", level=level)
    orchestrator.wait_for_command_result(sequence, timeout=timeout)
    orchestrator.wait_for_scene(expected_scene=level, timeout=timeout)
    print(f"Sandbox ready: {level!r}.", flush=True)


def ensure_fresh_sandbox(*, orchestrator: BesiegeOrchestrator, timeout: float,
                         level: str = DEFAULT_SANDBOX_LEVEL) -> None:
    """Unload the current sandbox before loading a new machine into it.

    A same-scene reload cannot be distinguished from the old scene heartbeat.
    Go through another supported sandbox so each scene wait observes an actual
    transition, and stale building objects are destroyed before machine load.
    """
    if orchestrator.read_state().get("scene") == level:
        intermediate = "LONE ORB" if level == "BARREN EXPANSE" else "BARREN EXPANSE"
        ensure_sandbox(orchestrator=orchestrator, timeout=timeout, level=intermediate)
    ensure_sandbox(orchestrator=orchestrator, timeout=timeout, level=level)


def quit_game(*, orchestrator: BesiegeOrchestrator, timeout: float, poll_interval: float = 0.25) -> str:
    """Ask the running game to quit; force-kill if Application.Quit hangs."""
    pids_before = find_besiege_pids()
    if not pids_before:
        return "no_process"
    sequence = orchestrator.send_command("quit")
    try:
        orchestrator.wait_for_command_result(sequence, timeout=timeout)
    except OrchestratorTimeoutError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_besiege_pids():
            return "quit"
        time.sleep(poll_interval)
    remaining = find_besiege_pids()
    for pid in remaining:
        force_kill(pid)
    return "force_killed"
