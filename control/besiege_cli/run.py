"""The unified one-shot runner behind `besiege_cli run`.

One command, three business inputs::

    uv run python -m besiege_cli run --bsg machine.bsg --controller policy.py   --sandbox "BARREN EXPANSE"
    uv run python -m besiege_cli run --bsg machine.bsg --controller timeline.json --sandbox "BARREN EXPANSE"

The controller kind is determined by file suffix only (.py = live Python
subprocess, .json = timeline replayed by the mod); any other suffix is an
error, never a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from controller_sdk.client import ControllerClient
from controller_sdk.channel_bindings import BINDINGS_FILE, ENV_CONTROL_BINDINGS
from controller_sdk.snapshot import SnapshotUnavailableError
from controller_sdk.profiles import FULL_MACHINE_FIELDS, FULL_TARGET_FIELDS
from controller_sdk.protocol import (
    CAMERA_POSE_FILE,
    CONTROL_SUBSCRIPTION_FILE,
    CONTROL_SUBSCRIPTION_SCHEMA,
    ENV_CHANNEL_CATALOG,
    ENV_MACHINE_BSG,
    ENV_MOD_DATA_DIR,
    ENV_RUN_DIR,
    ENV_RUN_ID,
    PROTOCOL_BENCHMARK_FLAG_FILE,
    PROTOCOL_BENCHMARK_REPORT_FILE,
    TELEMETRY_MACHINE_SHA256_KEY,
    TELEMETRY_SAMPLE_RATE_HZ,
    TELEMETRY_SOURCE_SHA256_KEY,
    TIMELINE_FILE,
    atomic_write_json,
    normalize_telemetry_target_guids,
    read_shared_bytes,
)
from controller_sdk.telemetry_codec import TelemetryCodecError, decode_telemetry_marker

from .machine import infer_channels, parse_bsg, prepare_machine_bsg
from .manifest import (
    CONTROLLER_KIND_LIVE,
    CONTROLLER_KIND_TIMELINE,
    clear_run_state,
    file_sha256,
    new_run_id,
    write_run_manifest,
)
from .orchestrator import BesiegeOrchestrator, OrchestratorTimeoutError
from .compat import verify_compatibility
from .control_bindings import build_control_bindings
from .paths import datacache_dir, mod_data_dir, resolve_besiege_data, resolve_channel_catalog
from .preflight import PreflightError, expand_run_telemetry, reject_legacy_bsg
from .recorder import is_recording, recording_state_path, start_recording, stop_recording
from .run_status import RunStatus
from .session import ensure_game, ensure_sandbox
from .timeline import (
    installed_timeline_payload,
    read_timeline_json,
    resolve_timeline_events,
    shift_timeline_events,
    write_timeline_json,
)

DEFAULT_RUN_HOLD_SECONDS = 3.0


class RunFailedError(RuntimeError):
    """The run terminated on an explicit error reported by the mod or the
    controller subprocess."""


class _RunnerLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._handle = path.open("w", encoding="utf-8", newline="\n")

    def write(self, message: str) -> None:
        line = message if message.endswith("\n") else message + "\n"
        self._handle.write(line)
        self._handle.flush()
        print(message)

    def close(self) -> None:
        self._handle.close()


def resolve_controller_kind(controller_path: Path) -> str:
    suffix = controller_path.suffix.lower()
    if suffix == ".py":
        return CONTROLLER_KIND_LIVE
    if suffix == ".json":
        return CONTROLLER_KIND_TIMELINE
    raise ValueError(
        f"Unsupported controller file suffix {suffix!r} ({controller_path}). "
        "Use a .py Python controller program (live control) or a .json control "
        "timeline (mod-side replay); no other controller kinds exist."
    )


def _raise_on_run_error(state: dict) -> None:
    timing_error = str(state.get("timing_error", "") or "")
    if timing_error:
        raise RunFailedError(
            f"Controller mod reported timing_error={timing_error!r}: the game's fixed timestep "
            "cannot schedule 25 Hz telemetry / 10 Hz control on integer physics-frame boundaries. "
            "This is a compatibility failure; the mod refuses to run at an approximated rate."
        )
    run_error = str(state.get("run_error", "") or "")
    if run_error:
        if "rigidbody_destroyed" in run_error:
            print(
                f"Controller mod reported run_error={run_error!r}; "
                "continuing because block loss is a lifecycle event, not a run failure.",
                flush=True,
            )
            return
        raise RunFailedError(
            f"Controller mod reported run_error={run_error!r} "
            f"(machine_run_id={state.get('machine_run_id')!r}, manifest run_id={state.get('run_id')!r})."
        )


def _wait_playback_finished(
    orchestrator: BesiegeOrchestrator,
    *,
    timeout: float,
    poll_interval: float,
) -> dict:
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = orchestrator.read_state()
        _raise_on_run_error(state)
        if bool(state.get("playback_finished")):
            return state
        time.sleep(poll_interval)
    raise OrchestratorTimeoutError(
        f"Timeline playback did not finish within {timeout}s "
        f"(events_applied={state.get('events_applied')}/{state.get('events_total')})."
    )


def _stop_simulation(orchestrator: BesiegeOrchestrator, timeout: float) -> None:
    sequence = orchestrator.send_command("stop_sim")
    orchestrator.wait_for_command_result(sequence, timeout=timeout)
    orchestrator.wait_for_simulating(False, timeout=timeout)


def _format_cleanup_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "(none)"
    return ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))


def _write_input_manifest(
    run_dir: Path,
    *,
    run_id: str,
    source_bsg: Path,
    prepared_bsg: Path,
    controller_path: Path,
    controller_kind: str,
    sandbox: str,
    selection: dict[str, Any],
    recorder_enabled: bool,
    recorder_hz: float,
    source_sha256: str,
    machine_sha256: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    payload = {
        "schema": "buildarena.input_manifest.v1",
        "run_id": run_id,
        "source_bsg": str(source_bsg.resolve()),
        "source_sha256": source_sha256,
        "prepared_bsg": str(prepared_bsg.resolve()),
        "machine_sha256": machine_sha256,
        "controller": str(controller_path.resolve()),
        "controller_kind": controller_kind,
        "sandbox": sandbox,
        "telemetry": {
            "profile": selection.get("profile"),
            "target_fields": list(selection.get("target_fields") or ()),
            "machine_fields": list(selection.get("machine_fields") or ()),
            "telemetry_hz": args.telemetry_hz,
        },
        "recorder": {
            "enabled": recorder_enabled,
            "hz": recorder_hz,
            "profile": "full",
            "targets": "all_machine_blocks",
        },
        "screen_record": bool(getattr(args, "record", False)),
        "pre_controller_hold": float(getattr(args, "pre_controller_hold", DEFAULT_RUN_HOLD_SECONDS) or 0.0),
        "post_completion_hold": float(getattr(args, "post_completion_hold", DEFAULT_RUN_HOLD_SECONDS) or 0.0),
        "camera_follow": getattr(args, "camera_follow", None),
        "bulk": {
            "enabled": args.bulk_hz is not None or args.bulk_batch is not None,
            "hz": args.bulk_hz,
            "batch_samples": args.bulk_batch,
            "inherits": "live_selection",
        },
    }
    atomic_write_json(run_dir / "input_manifest.json", payload)
    return payload


def _copy_controller_snapshot(run_dir: Path, controller_path: Path, kind: str) -> None:
    if kind == CONTROLLER_KIND_LIVE:
        shutil.copy2(controller_path, run_dir / "controller_snapshot.py")


def _read_btm4_snapshot(data_dir: Path) -> dict[str, Any]:
    marker_path = data_dir / "tm"
    if not marker_path.is_file():
        return {}
    try:
        marker = decode_telemetry_marker(read_shared_bytes(marker_path))
    except (OSError, TelemetryCodecError, RuntimeError):
        return {}
    return {
        "last_telemetry_sequence": int(marker.sequence),
        "last_action_ack": int(marker.sequence_applied),
        "simulation_time": None,
    }


def _refresh_live_status(
    status: RunStatus,
    *,
    data_dir: Path,
    process: subprocess.Popen[Any] | None,
    recording: bool,
    recorder_enabled: bool,
) -> None:
    snapshot = _read_btm4_snapshot(data_dir)
    if process is None:
        controller_status = status.payload.get("controller_status") or ""
        controller_pid = status.payload.get("controller_pid")
    elif process.poll() is None:
        controller_status = "running"
        controller_pid = process.pid
    else:
        controller_status = f"exited:{process.returncode}"
        controller_pid = process.pid
    screen = "recording" if recording and is_recording(data_dir) else (
        "stopped" if recording else "disabled"
    )
    telemetry_recorder = "enabled" if recorder_enabled else "disabled"
    if snapshot:
        status.refresh(
            last_telemetry_sequence=snapshot["last_telemetry_sequence"],
            last_action_ack=snapshot["last_action_ack"],
            controller_pid=controller_pid,
            controller_status=controller_status,
            screen_recorder_status=screen,
            telemetry_recorder_status=telemetry_recorder,
        )
    else:
        status.refresh(
            controller_pid=controller_pid,
            controller_status=controller_status,
            screen_recorder_status=screen,
            telemetry_recorder_status=telemetry_recorder,
        )


def _hold_wall_timeout(hold_seconds: float, explicit_timeout: float) -> float:
    if explicit_timeout > 0:
        return explicit_timeout
    return max(hold_seconds * 4.0, hold_seconds + 15.0)


def _hold_for_sim_time(
    data_dir: Path,
    *,
    hold_seconds: float,
    wall_timeout: float,
    poll_interval: float,
    label: str,
) -> None:
    if hold_seconds <= 0:
        return
    client = ControllerClient(data_dir, poll_interval=poll_interval)
    deadline = time.monotonic() + wall_timeout
    start = client.next_sample(timeout=wall_timeout)
    if not start.simulating:
        raise RunFailedError(
            f"{label} requires a live BAT4 stream; simulation is not running."
        )
    target = float(start.simulation_time) + hold_seconds
    last_sequence = int(start.sequence)
    last_sim = float(start.simulation_time)
    while time.monotonic() < deadline:
        try:
            frame = client.read_sample(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
        except SnapshotUnavailableError:
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
            continue
        if int(frame.sequence) == last_sequence or float(frame.simulation_time) <= last_sim:
            time.sleep(poll_interval)
            continue
        last_sequence = int(frame.sequence)
        last_sim = float(frame.simulation_time)
        if last_sim >= target:
            return
        time.sleep(poll_interval)
    raise RunFailedError(
        f"{label} did not reach {hold_seconds}s of BAT4 simulation time "
        f"within {wall_timeout}s wall-clock (last_sim={last_sim:.3f}, target={target:.3f})."
    )


def _release_controls(data_dir: Path, *, run_id: str, timeout: float) -> None:
    client = ControllerClient(data_dir, run_id=run_id, poll_interval=0.05)
    if client.block_table_path.exists() and client._channels_by_index is None:
        client.load_block_table()
    # A well-behaved live controller closes and disarms itself before its
    # subprocess exits. Re-arm this runner-owned final empty snapshot so the
    # mod polls and acknowledges it; close() always disarms again.
    client.arm()
    client.close(ack_timeout=timeout)


def _wait_controller_ready(path: Path, *, run_id: str, process, timeout: float) -> None:
    """Wait for this process to finish initialization before starting physics."""
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RunFailedError("Controller exited before publishing its readiness handshake.")
        try:
            ready = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError):
            # Windows may briefly deny reads while the atomic handshake is
            # being published. Retry only within the existing readiness deadline.
            ready = None
        if ready is not None:
            if ready != {"schema": "buildarena.controller_ready.v1", "run_id": run_id}:
                raise RunFailedError("Controller readiness handshake has a stale run ID or invalid schema.")
            return
        if time.monotonic() >= deadline:
            raise RunFailedError("Controller did not publish its readiness handshake before the deadline.")
        time.sleep(min(.01, max(0., deadline-time.monotonic())))


def _run_python_controller(
    *,
    controller_path: Path,
    run_id: str,
    data_dir: Path,
    prepared_bsg: Path,
    catalog_path: Path,
    run_dir: Path,
    timeout: float,
    log: _RunnerLog,
    status: RunStatus,
    stop_refresh: threading.Event,
    recorder_enabled: bool,
    recording: bool,
    start_simulation: Callable[[], None] | None = None,
) -> None:
    environment = dict(os.environ)
    environment[ENV_RUN_ID] = run_id
    environment[ENV_MOD_DATA_DIR] = str(data_dir.resolve())
    environment[ENV_MACHINE_BSG] = str(prepared_bsg.resolve())
    environment[ENV_CHANNEL_CATALOG] = str(Path(catalog_path).resolve())
    environment[ENV_RUN_DIR] = str(run_dir.resolve())
    environment[ENV_CONTROL_BINDINGS] = str((run_dir / BINDINGS_FILE).resolve())
    ready_path = run_dir / "controller_ready.json"
    if start_simulation is not None:
        ready_path.unlink(missing_ok=True)
        environment["BUILDARENA_CONTROLLER_READY"] = str(ready_path.resolve())
    else:
        environment.pop("BUILDARENA_CONTROLLER_READY", None)
    control_root = str(Path(__file__).resolve().parents[1])
    existing_python_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        control_root + os.pathsep + existing_python_path if existing_python_path else control_root
    )

    controller_log = run_dir / "logs" / "controller.log"
    controller_log.parent.mkdir(parents=True, exist_ok=True)
    log.write(f"Starting controller subprocess: {sys.executable} {controller_path}")
    log.write("Controller stdout is mirrored here so long waits are visible.")
    with controller_log.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            [sys.executable, str(controller_path)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        status.refresh(controller_pid=process.pid, controller_status="running")

        def _status_loop() -> None:
            while not stop_refresh.is_set():
                _refresh_live_status(
                    status,
                    data_dir=data_dir,
                    process=process,
                    recording=recording,
                    recorder_enabled=recorder_enabled,
                )
                stop_refresh.wait(1.0)

        def _pump_stdout() -> None:
            assert process.stdout is not None
            for raw in process.stdout:
                handle.write(raw)
                handle.flush()
                message = raw.rstrip("\r\n")
                if message:
                    log.write(f"controller: {message}")

        refresher = threading.Thread(target=_status_loop, name="run-status", daemon=True)
        pump = threading.Thread(target=_pump_stdout, name="controller-stdout", daemon=True)
        refresher.start()
        pump.start()
        try:
            deadline = time.monotonic() + timeout
            if start_simulation is not None:
                _wait_controller_ready(ready_path, run_id=run_id, process=process, timeout=min(15., timeout))
                log.write("Controller initialized and armed; starting simulation.")
                start_simulation()
            exit_code = process.wait(timeout=max(.001, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
            raise RunFailedError(
                f"Controller subprocess {controller_path.name} exceeded --controller-timeout={timeout}s "
                "and was terminated."
            ) from None
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10.)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10.)
            stop_refresh.set()
            pump.join(timeout=2.0)
            refresher.join(timeout=2.0)
            if process.stdout is not None and not pump.is_alive():
                process.stdout.close()
        if exit_code != 0:
            raise RunFailedError(
                f"Controller subprocess {controller_path.name} exited with code {exit_code}."
            )
    log.write("Controller subprocess finished (exit code 0).")
    status.refresh(controller_status="exited:0")


def _write_live_telemetry_stats(run_dir: Path, data_dir: Path) -> None:
    snapshot = _read_btm4_snapshot(data_dir)
    atomic_write_json(
        run_dir / "live_telemetry_stats.json",
        {
            "last_telemetry_sequence": snapshot.get("last_telemetry_sequence"),
            "last_action_ack": snapshot.get("last_action_ack"),
        },
    )


def cmd_run(args: argparse.Namespace) -> int:
    recorder_enabled = not bool(getattr(args, "no_recorder", False))
    recorder_hz_arg = getattr(args, "recorder_hz", None)
    if not recorder_enabled and recorder_hz_arg is not None:
        raise PreflightError("--recorder-hz cannot be combined with --no-recorder.")
    recorder_hz = float(recorder_hz_arg if recorder_hz_arg is not None else TELEMETRY_SAMPLE_RATE_HZ)
    pre_hold = float(getattr(args, "pre_controller_hold", DEFAULT_RUN_HOLD_SECONDS) or 0.0)
    post_hold = float(getattr(args, "post_completion_hold", DEFAULT_RUN_HOLD_SECONDS) or 0.0)
    if pre_hold < 0 or post_hold < 0:
        raise PreflightError("--pre-controller-hold and --post-completion-hold must be >= 0.")
    if args.bulk_hz is None and args.bulk_batch is not None:
        raise PreflightError("--bulk-hz and --bulk-batch must be given together.")
    if args.bulk_batch is None and args.bulk_hz is not None:
        raise PreflightError("--bulk-hz and --bulk-batch must be given together.")

    besiege_data = resolve_besiege_data(args.besiege_data)
    compat_notice = verify_compatibility(besiege_data)

    data_dir = mod_data_dir(besiege_data)
    orchestrator = BesiegeOrchestrator(data_dir, poll_interval=args.poll_interval)

    bsg_path = Path(args.bsg)
    if not bsg_path.is_file():
        raise FileNotFoundError(f"BSG file not found: {bsg_path}")
    reject_legacy_bsg(bsg_path)
    controller_path = Path(args.controller)
    if not controller_path.is_file():
        raise FileNotFoundError(f"Controller file not found: {controller_path}")
    controller_kind = resolve_controller_kind(controller_path)
    controller_prestart = bool(getattr(args, "controller_prestart", False))
    if controller_prestart and (controller_kind != CONTROLLER_KIND_LIVE or pre_hold != 0):
        raise PreflightError("--controller-prestart requires a live Python controller and --pre-controller-hold 0.")

    catalog_path = resolve_channel_catalog(args.catalog)
    run_id = new_run_id()
    experiment = getattr(args, "experiment", None)
    run_dir = (
        Path(args.run_dir)
        if args.run_dir
        else (
            datacache_dir()
            / "control_experiments"
            / (experiment or "unspecified")
            / "runs"
            / f"{time.strftime('%Y%m%d-%H%M%S')}-{run_id[:8]}"
        )
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    log = _RunnerLog(run_dir / "logs" / "runner.log")
    status = RunStatus(run_dir / "run_status.json", run_id=run_id)
    if compat_notice:
        log.write(compat_notice)
    log.write(f"Controller recognized: {controller_path.name} -> kind={controller_kind}")

    status.set_phase("prepared")
    selection = expand_run_telemetry(args)
    prepared_bsg = run_dir / f"{bsg_path.stem}_prepared.bsg"
    prepare_machine_bsg(
        bsg_path,
        prepared_bsg,
        run_id=run_id,
        recorder_enabled=recorder_enabled,
        recorder_hz=recorder_hz,
    )
    _, _, prepared_data = parse_bsg(prepared_bsg, catalog_path=None)
    source_sha256 = str(prepared_data.get(TELEMETRY_SOURCE_SHA256_KEY) or file_sha256(bsg_path))
    machine_sha256 = str(prepared_data.get(TELEMETRY_MACHINE_SHA256_KEY) or "")
    input_manifest = _write_input_manifest(
        run_dir,
        run_id=run_id,
        source_bsg=bsg_path,
        prepared_bsg=prepared_bsg,
        controller_path=controller_path,
        controller_kind=controller_kind,
        sandbox=args.sandbox,
        selection=selection,
        recorder_enabled=recorder_enabled,
        recorder_hz=recorder_hz,
        source_sha256=source_sha256,
        machine_sha256=machine_sha256,
        args=args,
    )
    _copy_controller_snapshot(run_dir, controller_path, controller_kind)
    log.write(f"Run id: {run_id}")
    log.write(f"Prepared BSG (original untouched): {prepared_bsg}")

    _tree, prepared_blocks, _machine_data = parse_bsg(
        prepared_bsg, catalog_path=catalog_path
    )
    channels = infer_channels(prepared_blocks, catalog_path=catalog_path)
    atomic_write_json(
        run_dir / BINDINGS_FILE,
        build_control_bindings(prepared_blocks, channels, run_id=run_id),
        durable=False,
    )

    resolved_events: list[dict[str, object]] | None = None
    if controller_kind == CONTROLLER_KIND_TIMELINE:
        timeline = read_timeline_json(controller_path)
        resolved_events = shift_timeline_events(
            resolve_timeline_events(timeline["events"], channels),
            pre_hold,
        )
        resolved_timeline = installed_timeline_payload(resolved_events, run_id=run_id)
        write_timeline_json(run_dir / "timeline_resolved.json", resolved_timeline)
        log.write(
            f"Timeline validated: {len(resolved_events)} events against {len(channels)} channels."
        )
        if pre_hold > 0:
            log.write(
                f"Timeline events shifted by {pre_hold}s so the first actuation "
                "waits out the pre-controller hold."
            )

    log.write(
        f"Ensuring Besiege is running (launch-timeout={args.launch_timeout:.0f}s). "
        "After the window appears, ToolKit heartbeat and sandbox load can still take a minute."
    )
    ensure_game(orchestrator=orchestrator, besiege_data=besiege_data, timeout=args.launch_timeout)
    ensure_sandbox(orchestrator=orchestrator, timeout=args.timeout, level=args.sandbox)
    log.write(f"Sandbox ready: {args.sandbox!r}")

    removed = clear_run_state(data_dir)
    if removed:
        log.write(f"Removed stale run state: {_format_cleanup_counts(removed)}")
    tracked_guids = list(normalize_telemetry_target_guids(args.track_guids))
    if args.track_blocks:
        from .machine import guids_for_block_indices

        tracked_guids = list(
            normalize_telemetry_target_guids(
                tracked_guids
                + guids_for_block_indices(prepared_blocks, args.track_blocks)
            )
        )
    if tracked_guids:
        log.write(
            f"BAT4 subset: {len(tracked_guids)} block(s) "
            f"(user-facing indices: {list(args.track_blocks)})."
        )
    else:
        log.write("BAT4 targets: all machine blocks.")

    installed_name = orchestrator.install_machine(
        source_bsg=prepared_bsg, name=f"{bsg_path.stem}_{run_id[:8]}.bsg"
    )
    log.write(f"Loading machine {installed_name} into the current sandbox...")
    sequence = orchestrator.send_command("load_machine", path=installed_name)
    orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
    log.write(f"Machine loaded: {installed_name}")

    if controller_kind == CONTROLLER_KIND_TIMELINE:
        assert resolved_events is not None
        atomic_write_json(data_dir / TIMELINE_FILE, installed_timeline_payload(resolved_events, run_id=run_id))
    write_run_manifest(
        data_dir,
        run_id=run_id,
        controller_kind=controller_kind,
        machine_file=installed_name,
        machine_sha256=file_sha256(prepared_bsg),
        timeline_file=TIMELINE_FILE if controller_kind == CONTROLLER_KIND_TIMELINE else None,
    )
    log.write("Run manifest written; mod will bind to this run id.")
    camera_follow = getattr(args, "camera_follow", None)
    if camera_follow:
        distance = getattr(args, "camera_distance", None)
        pitch = getattr(args, "camera_pitch", None)
        tokens = ["follow", camera_follow]
        if distance is not None:
            tokens.append(str(distance))
        if pitch is not None:
            tokens.append(str(pitch))
        (data_dir / CAMERA_POSE_FILE).write_text(" ".join(tokens) + "\n", encoding="ascii")
        log.write(f"Camera pose written: {' '.join(tokens)}")

    needs_live_stream = controller_kind == CONTROLLER_KIND_LIVE or pre_hold > 0 or post_hold > 0
    if needs_live_stream:
        subscription: dict[str, object] = {
            "schema": CONTROL_SUBSCRIPTION_SCHEMA,
            "run_id": run_id,
            "telemetry_hz": args.telemetry_hz,
            "target_field_mask": selection["target_field_mask"],
            "target_fields": list(selection["target_fields"]),
            "machine_field_mask": selection["machine_field_mask"],
            "machine_fields": list(selection["machine_fields"]),
            "target_guids": tracked_guids,
        }
        if selection["profile"] is not None:
            subscription["profile"] = selection["profile"]
        if args.bulk_hz is not None and args.bulk_batch is not None:
            subscription["bulk_target_field_mask"] = selection["target_field_mask"]
            subscription["bulk_target_fields"] = list(selection["target_fields"])
            subscription["bulk_machine_field_mask"] = selection["machine_field_mask"]
            subscription["bulk_machine_fields"] = list(selection["machine_fields"])
            if selection["profile"] is not None:
                subscription["bulk_profile"] = selection["profile"]
            subscription["bulk_hz"] = args.bulk_hz
            subscription["bulk_batch_samples"] = args.bulk_batch
        atomic_write_json(
            data_dir / CONTROL_SUBSCRIPTION_FILE,
            subscription,
            durable=False,
        )
        log.write(
            f"BAT4 subscription written: "
            f"{'all blocks' if not tracked_guids else f'{len(tracked_guids)} target(s)'}, "
            f"{args.telemetry_hz} Hz, profile={selection['profile']!r}."
        )
        benchmark_report = data_dir / PROTOCOL_BENCHMARK_REPORT_FILE
        benchmark_report.unlink(missing_ok=True)
        if args.protocol_benchmark:
            (data_dir / PROTOCOL_BENCHMARK_FLAG_FILE).write_text(
                f"{run_id}\n", encoding="ascii"
            )

    simulation_started = False
    simulation_start_requested = False
    recording_path: Path | None = None
    run_failed = False
    critical_errors: list[str] = []
    stop_refresh = threading.Event()
    def start_simulation() -> None:
        nonlocal simulation_started, simulation_start_requested
        sequence = orchestrator.send_command("start_sim")
        simulation_start_requested = True
        orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
        orchestrator.wait_for_simulating(True, timeout=args.timeout)
        simulation_started = True
        _raise_on_run_error(orchestrator.read_state())
        log.write("Simulation started.")
        status.set_phase("simulating")

    try:
        if getattr(args, "record", False):
            recording_path = run_dir / "run.mp4"
            start_recording(
                data_dir=data_dir,
                output=recording_path,
                fps=getattr(args, "record_fps", 25),
            )
            status.refresh(screen_recorder_status="recording")
            log.write("Screen recorder ready.")

        if not controller_prestart:
            start_simulation()

        if pre_hold > 0:
            status.set_phase("pre_controller_hold")
            _hold_for_sim_time(
                data_dir,
                hold_seconds=pre_hold,
                wall_timeout=_hold_wall_timeout(
                    pre_hold,
                    float(getattr(args, "pre_controller_hold_timeout", 0.0) or 0.0),
                ),
                poll_interval=args.poll_interval,
                label="pre_controller_hold",
            )
            log.write(f"Pre-controller hold reached {pre_hold}s of BAT4 simulation time.")
            status.set_phase("simulating")

        if controller_kind == CONTROLLER_KIND_TIMELINE:
            state = _wait_playback_finished(
                orchestrator, timeout=args.playback_timeout, poll_interval=args.poll_interval
            )
            log.write(
                f"Playback finished (events_applied={state.get('events_applied')}"
                f"/{state.get('events_total')})."
            )
        else:
            _run_python_controller(
                controller_path=controller_path,
                run_id=run_id,
                data_dir=data_dir,
                prepared_bsg=prepared_bsg,
                catalog_path=catalog_path,
                run_dir=run_dir,
                timeout=args.controller_timeout,
                log=log,
                status=status,
                stop_refresh=stop_refresh,
                recorder_enabled=recorder_enabled,
                recording=recording_path is not None,
                start_simulation=start_simulation if controller_prestart else None,
            )
            _raise_on_run_error(orchestrator.read_state())
        status.set_phase("controller_finished")
    except Exception as exc:
        run_failed = True
        status.set_phase("failed", result="failed", error=str(exc))
        status.append_critical_error(str(exc))
        critical_errors.append(str(exc))
        log.write(f"Run failed: {exc}")
    finally:
        stop_refresh.set()
        try:
            _release_controls(data_dir, run_id=run_id, timeout=args.timeout)
            log.write("Control released.")
        except Exception as exc:  # noqa: BLE001
            message = f"control release failed: {exc}"
            status.append_critical_error(message)
            status.append_cleanup_error(message)
            critical_errors.append(message)
            log.write(message)

        if post_hold > 0 and simulation_started:
            try:
                status.set_phase("holding")
                _hold_for_sim_time(
                    data_dir,
                    hold_seconds=post_hold,
                    wall_timeout=_hold_wall_timeout(
                        post_hold,
                        float(getattr(args, "post_completion_hold_timeout", 0.0) or 0.0),
                    ),
                    poll_interval=args.poll_interval,
                    label="post_completion_hold",
                )
                log.write(f"Post-completion hold reached {post_hold}s of BAT4 simulation time.")
            except Exception as exc:  # noqa: BLE001
                message = f"post_completion_hold failed: {exc}"
                status.append_critical_error(message)
                status.append_cleanup_error(message)
                critical_errors.append(message)
                log.write(message)

        if recording_path is not None:
            try:
                recorder_log = run_dir / "logs" / "recorder.log"
                state_path = recording_state_path(data_dir)
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    source = Path(state.get("log_file", ""))
                    if source.exists():
                        shutil.copy2(source, recorder_log)
                video = stop_recording(data_dir=data_dir)
                status.refresh(screen_recorder_status="stopped")
                log.write(f"Screen recording saved: {video}")
            except Exception as exc:  # noqa: BLE001
                message = f"recorder stop failed: {exc}"
                status.append_critical_error(message)
                status.append_cleanup_error(message)
                critical_errors.append(message)
                log.write(message)

        if simulation_started or simulation_start_requested:
            try:
                _stop_simulation(orchestrator, timeout=args.timeout)
                log.write("Simulation stopped.")
            except (OrchestratorTimeoutError, Exception) as exc:  # noqa: BLE001
                message = f"stop_sim failed: {exc}"
                status.append_critical_error(message)
                status.append_cleanup_error(message)
                critical_errors.append(message)
                log.write(message)

        from telemetry.finalize import RecorderBindingError, finalize_run, wait_for_bound_manifest

        try:
            _write_live_telemetry_stats(run_dir, data_dir)
            if recorder_enabled:
                status.set_phase("waiting_recorder")
                wait_for_bound_manifest(
                    data_dir,
                    run_id=run_id,
                    machine_sha256=str(input_manifest["machine_sha256"]),
                    source_sha256=str(input_manifest["source_sha256"]),
                    timeout=args.timeout,
                )
            summary = finalize_run(
                run_dir=run_dir,
                data_dir=data_dir,
                selection=selection,
                extra={"result": "failed" if (run_failed or critical_errors) else "ok"},
                run_id=run_id,
                machine_sha256=str(input_manifest["machine_sha256"]),
                source_sha256=str(input_manifest["source_sha256"]),
                recorder_enabled=recorder_enabled,
            )
        except (RecorderBindingError, Exception) as exc:  # noqa: BLE001
            message = f"recorder/finalize failed: {exc}"
            status.append_critical_error(message)
            status.append_cleanup_error(message)
            critical_errors.append(message)
            log.write(message)
            summary = {
                "schema": "buildarena.run_summary.v1",
                "result": "failed",
                "omitted_outputs": [message],
            }
            atomic_write_json(run_dir / "summary.json", summary)

        cleaned = clear_run_state(data_dir)
        if cleaned:
            log.write(f"Cleared run state: {_format_cleanup_counts(cleaned)}")

    failed = run_failed or bool(critical_errors)
    status.update(
        phase="failed" if failed else "completed",
        result="failed" if failed else "ok",
    )
    if failed:
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            try:
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
            existing["result"] = "failed"
            atomic_write_json(summary_path, existing)
        log.write(f"Run failed: run_id={run_id}, outputs in {run_dir}")
        log.close()
        return 1
    log.write(f"Run complete: run_id={run_id}, outputs in {run_dir}")
    log.close()
    return 0


def add_run_parser(subparsers: argparse._SubParsersAction, common_args) -> None:
    parser = subparsers.add_parser(
        "run",
        help="One-shot run: prepare a temporary BSG, launch the game, enter the sandbox, "
        "load the machine, and execute a .py (live) or .json (timeline) controller.",
    )
    common_args(parser)
    parser.add_argument("--bsg", required=True, help="Path to the source .bsg machine file (never modified).")
    parser.add_argument(
        "--controller",
        required=True,
        help="Controller file: a .py Python program (live control via ControllerClient) or a "
        ".json control timeline (buildarena.control_timeline.v2).",
    )
    parser.add_argument(
        "--sandbox",
        default="BARREN EXPANSE",
        help="Sandbox level scene name to run in (default: 'BARREN EXPANSE').",
    )
    parser.add_argument("--catalog", default=None, help="Override the ToolKit-data-dir block_channel_catalog.json path.")
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Directory for this run's outputs (prepared BSG, resolved timeline); "
        "default: <repo>/datacache/runs/<timestamp>-<run id>.",
    )
    parser.add_argument("--experiment", default=None, help="Experiment name used in the default run directory.")
    parser.add_argument(
        "--track-guids",
        nargs="*",
        default=(),
        help="Optional BAT4 GUID subset. Omit to collect every simulation block.",
    )
    parser.add_argument(
        "--track-blocks",
        nargs="*",
        type=int,
        default=(),
        metavar="N",
        help="Optional BAT4 subset by inspect-machine index. Omit to collect every block.",
    )
    parser.add_argument(
        "--telemetry-hz",
        type=int,
        choices=(10, 25, 50),
        default=25,
        help="BAT4 telemetry frequency aligned to physics frames (default: 25).",
    )
    parser.add_argument(
        "--telemetry-profile",
        choices=("full", "position-only", "custom"),
        default="full",
        help="Access width: full (all dynamic fields) or position-only (default: full).",
    )
    parser.add_argument(
        "--telemetry-fields",
        nargs="+",
        choices=FULL_TARGET_FIELDS,
        default=None,
        help="Custom live target fields. Mutually exclusive with --telemetry-profile full/position-only.",
    )
    parser.add_argument(
        "--machine-fields",
        nargs="+",
        choices=FULL_MACHINE_FIELDS,
        default=None,
        help="Custom live machine fields. Use with --telemetry-profile custom.",
    )
    parser.add_argument("--record", action="store_true", help="Record the Besiege window to run.mp4.")
    parser.add_argument("--controller-prestart", action="store_true",
                        help="Initialize a cooperating Python controller before physics; requires its readiness handshake and --pre-controller-hold 0.")
    parser.add_argument("--record-fps", type=int, default=25)
    parser.add_argument("--camera-follow", default=None, help="Block GUID for MouseOrbit follow.")
    parser.add_argument("--camera-distance", type=float, default=None)
    parser.add_argument("--camera-pitch", type=float, default=None)
    parser.add_argument(
        "--pre-controller-hold",
        type=float,
        default=DEFAULT_RUN_HOLD_SECONDS,
        help=(
            "After simulation starts, wait this many BAT4 simulation seconds before "
            "launching the controller (default: 3). Timeline events are shifted by "
            "the same amount so the first actuation also waits."
        ),
    )
    parser.add_argument(
        "--pre-controller-hold-timeout",
        type=float,
        default=0.0,
        help="Wall-clock timeout for the pre-controller hold. Default: max(4*hold, hold+15).",
    )
    parser.add_argument(
        "--post-completion-hold",
        type=float,
        default=DEFAULT_RUN_HOLD_SECONDS,
        help=(
            "Keep simulation and recording running this many BAT4 simulation seconds "
            "after the controller finishes, then stop recording (default: 3)."
        ),
    )
    parser.add_argument(
        "--post-completion-hold-timeout",
        type=float,
        default=0.0,
        help="Wall-clock timeout for the post-completion hold. Default: max(4*hold, hold+15).",
    )
    parser.add_argument(
        "--recorder-hz",
        type=int,
        choices=(10, 25, 50, 100),
        default=None,
        help="Offline TelemetryRecorder frequency. Recorder is always full-machine full (default: 25).",
    )
    parser.add_argument(
        "--no-recorder",
        action="store_true",
        help="Disable the offline TelemetryRecorder for this managed run.",
    )
    parser.add_argument(
        "--bulk-hz",
        type=int,
        choices=(10, 25, 50),
        default=None,
        help="Enable BAB4 bulk telemetry at this frequency. Fields inherit the live BAT4 selection.",
    )
    parser.add_argument(
        "--bulk-batch",
        type=int,
        default=None,
        help="BAB4 samples accumulated per bulk file write (1-100). Requires --bulk-hz.",
    )
    parser.add_argument(
        "--protocol-benchmark",
        action="store_true",
        help="Ask the mod to write protocol_benchmark_report.json with FPS/GC deltas.",
    )
    parser.add_argument("--launch-timeout", type=float, default=120.0, help="Seconds to wait for the game + mod heartbeat.")
    parser.add_argument("--playback-timeout", type=float, default=300.0, help="Max seconds to wait for timeline playback.")
    parser.add_argument(
        "--controller-timeout",
        type=float,
        default=600.0,
        help="Max seconds a .py controller subprocess may run before it is terminated and the run fails.",
    )
    parser.set_defaults(func=cmd_run)
