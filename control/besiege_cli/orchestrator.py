from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from buildarena.progress import TimeBudgetBar
from controller_sdk.protocol import atomic_write_json, read_json_retry


class OrchestratorTimeoutError(TimeoutError):
    """A wait on the mod's orchestrator_state.json heartbeat/ack did not
    complete within its deadline."""


class OrchestratorCommandError(RuntimeError):
    """The mod executed a command and reported an explicit failure via
    last_command_status="error" / last_command_error."""


class BesiegeOrchestrator:
    """Python side of the mod's file-based orchestrator protocol.

    The matching mod-side implementation ships in BuildArena ToolKit 2.0.9.
    """

    def __init__(self, mod_data_dir: str | Path, poll_interval: float = 0.25):
        self.mod_data_dir = Path(mod_data_dir)
        self.command_path = self.mod_data_dir / "orchestrator_command.json"
        self.state_path = self.mod_data_dir / "orchestrator_state.json"
        self.poll_interval = poll_interval
        self.sequence = int(time.time() * 1000) % 1_000_000_000

    def install_machine(self, *, source_bsg: str | Path, name: str | None = None) -> str:
        """Copy a .bsg into the mod data dir (where load_machine resolves
        relative paths) and return the file name to pass to load_machine.

        Args:
            source_bsg: Path to the machine file to install.
            name: Installed file name; defaults to the source file's name.
        """
        source = Path(source_bsg)
        if not source.is_file():
            raise FileNotFoundError(f"Machine file not found: {source}")
        installed_name = name if name is not None else source.name
        shutil.copy2(source, self.mod_data_dir / installed_name)
        return installed_name

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        return read_json_retry(self.state_path)

    def state_mtime(self) -> float | None:
        if not self.state_path.exists():
            return None
        return self.state_path.stat().st_mtime

    def _progress_loop(
        self,
        *,
        timeout: float,
        label: str,
        ready: Callable[[], Any | None],
        detail: Callable[[], str],
    ) -> Any:
        """Poll until `ready` returns a non-None value, showing a time budget bar."""
        started = time.monotonic()
        deadline = started + timeout
        bar = TimeBudgetBar(timeout=timeout, desc=label)
        finished = False
        try:
            while time.monotonic() < deadline:
                result = ready()
                if result is not None:
                    finished = True
                    return result
                bar.poke(detail())
                time.sleep(self.poll_interval)
            raise OrchestratorTimeoutError(
                f"{label} did not finish within {timeout}s ({detail()})."
            )
        finally:
            bar.close(ok=finished)

    def wait_for_heartbeat(self, timeout: float, after_mtime: float | None = None) -> dict[str, Any]:
        """Block until a fresh orchestrator_state.json heartbeat appears.

        Used right after launching the process: the mod publishes this file
        at 2 Hz from the moment ControllerRunner.Start() runs, so its
        (re)appearance is proof the mod DLL loaded and is actually ticking,
        as opposed to the game process merely existing.
        """

        def ready() -> dict[str, Any] | None:
            if not self.state_path.exists():
                return None
            mtime = self.state_path.stat().st_mtime
            if after_mtime is not None and mtime <= after_mtime:
                return None
            return read_json_retry(self.state_path)

        def detail() -> str:
            if not self.state_path.exists():
                return f"no {self.state_path.name} yet; Besiege is still loading"
            return f"heartbeat file exists but is stale at {self.state_path}"

        try:
            return self._progress_loop(
                timeout=timeout,
                label="Waiting for BuildArena ToolKit heartbeat after Besiege load",
                ready=ready,
                detail=detail,
            )
        except OrchestratorTimeoutError:
            raise OrchestratorTimeoutError(
                f"No orchestrator_state.json heartbeat within {timeout}s at {self.state_path}."
            ) from None

    def send_command(self, command: str, **args: Any) -> int:
        # The time seed wraps every ~11.6 days; a running mod remembers its
        # previous sequence and ignores smaller values. Also respect commands
        # issued by another CLI instance since this object was constructed.
        state_sequence = int(self.read_state().get("last_command_sequence", -1))
        queued_sequence = -1
        if self.command_path.exists():
            queued_sequence = int(read_json_retry(self.command_path).get("sequence", -1))
        self.sequence = max(self.sequence, state_sequence, queued_sequence) + 1
        if self.sequence > 2_147_483_647:
            raise OrchestratorCommandError("Orchestrator sequence exhausted; restart Besiege and reset its command files.")
        payload: dict[str, Any] = {"sequence": self.sequence, "command": command}
        payload.update(args)
        atomic_write_json(self.command_path, payload)
        return self.sequence

    def wait_for_command_result(self, sequence: int, timeout: float) -> dict[str, Any]:
        """Block until the mod reports it processed this command sequence.

        Raises OrchestratorCommandError immediately on an explicit failure
        (no silent "maybe it worked" ambiguity) and OrchestratorTimeoutError
        if the mod never acknowledges the sequence at all within the
        deadline.
        """
        latest: dict[str, Any] = {}

        def ready() -> dict[str, Any] | None:
            nonlocal latest
            latest = self.read_state()
            acknowledged = int(latest.get("last_command_sequence", -1))
            if acknowledged < sequence:
                return None
            if acknowledged > sequence:
                raise OrchestratorCommandError(
                    f"Command sequence {sequence} was superseded by {acknowledged}; "
                    "another command's result cannot confirm this operation."
                )
            status = latest.get("last_command_status", "")
            if status == "ok":
                return latest
            if status == "error":
                raise OrchestratorCommandError(
                    f"Command sequence {sequence} ({latest.get('last_command')}) failed: "
                    f"{latest.get('last_command_error', '(no error detail)')}"
                )
            return None

        def detail() -> str:
            return (
                f"command sequence={sequence} "
                f"last_ack={latest.get('last_command_sequence', -1)} "
                f"scene={latest.get('scene')!r} simulating={latest.get('simulating')}"
            )

        try:
            return self._progress_loop(
                timeout=timeout,
                label=f"Waiting for orchestrator command sequence {sequence}",
                ready=ready,
                detail=detail,
            )
        except OrchestratorTimeoutError:
            raise OrchestratorTimeoutError(
                f"Command sequence {sequence} was not acknowledged within {timeout}s "
                f"(latest last_command_sequence={latest.get('last_command_sequence', -1)})."
            ) from None

    def wait_for_scene(self, expected_scene: str, timeout: float) -> dict[str, Any]:
        """Block until orchestrator_state.json reports the given scene name.

        Used by enter-sandbox: the enter_sandbox command is acknowledged
        before the (asynchronous) Unity level load completes, so the CLI must
        additionally wait for the scene field to flip to the target level.
        """
        latest: dict[str, Any] = {}

        def ready() -> dict[str, Any] | None:
            nonlocal latest
            latest = self.read_state()
            if latest.get("scene", "") == expected_scene:
                return latest
            return None

        def detail() -> str:
            return f"current scene={latest.get('scene')!r}, want {expected_scene!r}"

        try:
            return self._progress_loop(
                timeout=timeout,
                label=f"Waiting for sandbox scene {expected_scene!r}",
                ready=ready,
                detail=detail,
            )
        except OrchestratorTimeoutError:
            raise OrchestratorTimeoutError(
                f"Besiege did not reach scene={expected_scene!r} within {timeout}s "
                f"(latest scene={latest.get('scene')!r})."
            ) from None

    def wait_for_simulating(self, expected: bool, timeout: float) -> dict[str, Any]:
        latest: dict[str, Any] = {}

        def ready() -> dict[str, Any] | None:
            nonlocal latest
            latest = self.read_state()
            if bool(latest.get("simulating")) == expected:
                return latest
            return None

        def detail() -> str:
            return f"current simulating={latest.get('simulating')}, want {expected}"

        try:
            return self._progress_loop(
                timeout=timeout,
                label=f"Waiting for simulating={expected}",
                ready=ready,
                detail=detail,
            )
        except OrchestratorTimeoutError:
            raise OrchestratorTimeoutError(
                f"Besiege did not reach simulating={expected} within {timeout}s "
                f"(latest simulating={latest.get('simulating')})."
            ) from None
