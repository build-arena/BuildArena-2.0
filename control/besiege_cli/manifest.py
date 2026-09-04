"""Run manifest (buildarena.run_manifest.v1): the single source of truth
binding one `besiege_cli run` invocation to a prepared machine and its
controller.

The CLI writes run_manifest.json into the mod data dir before starting the
simulation; the mod refuses to replay a timeline or (for run-bound actions)
apply live controls unless the loaded machine's ``controller.run_id``
machine-data string matches the manifest. The CLI removes the manifest when
the run ends, so leftover state from a crashed run can never silently drive
a later machine.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

from controller_sdk.protocol import (
    ACTION_FILE_PREFIX,
    ACTION_FILE_SUFFIX,
    ARMED_FLAG_FILE,
    BLOCK_TABLE_FILE,
    BULK_BUFFER_FILES,
    BULK_PUBLISH_FILE,
    CAMERA_POSE_FILE,
    CONTROL_PROTOCOL_ERROR_FILE,
    LEGACY_PROTOCOL_FILES,
    PROTOCOL_BENCHMARK_FLAG_FILE,
    RUN_MANIFEST_FILE,
    RUN_MANIFEST_SCHEMA,
    SUBSCRIPTION_FILE,
    TELEMETRY_BUFFER_FILES,
    TELEMETRY_PUBLISH_FILE,
    TELEMETRY_RECORDER_ERROR_FILE,
    TIMELINE_FILE,
    parse_action_file_name,
    atomic_write_json,
)

RECORDING_PURGE_GLOBS = (
    "*__manifest.json",
    "*__traj_*.csv",
    "*__keys_*.csv",
)
LEFTOVER_EXPORT_FILES = (
    "recorded_keys.csv",
    "record_mode.flag",
    "control_channels.json",
    "control_channels.csv",
    "protocol_benchmark_report.json",
    "inspector_request.json",
    "inspector_report.json",
    "controller_runtime.log",
    "controller_probe.log",
    "orchestrator_command.json",
    "orchestrator_state.json",
    TELEMETRY_RECORDER_ERROR_FILE,
)

CONTROLLER_KIND_TIMELINE = "timeline"
CONTROLLER_KIND_LIVE = "live"


def new_run_id() -> str:
    return str(uuid.uuid4())


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_run_manifest(
    mod_data_dir: str | Path,
    *,
    run_id: str,
    controller_kind: str,
    machine_file: str,
    machine_sha256: str,
    timeline_file: str | None = None,
) -> Path:
    if controller_kind not in (CONTROLLER_KIND_TIMELINE, CONTROLLER_KIND_LIVE):
        raise ValueError(f"Unknown controller_kind {controller_kind!r}.")
    if controller_kind == CONTROLLER_KIND_TIMELINE and not timeline_file:
        raise ValueError("A timeline run manifest requires timeline_file.")
    payload: dict[str, object] = {
        "schema": RUN_MANIFEST_SCHEMA,
        "run_id": run_id,
        "controller_kind": controller_kind,
        "machine_file": machine_file,
        "machine_sha256": machine_sha256,
        "created_at": time.time(),
    }
    if timeline_file:
        payload["timeline_file"] = timeline_file
    manifest_path = Path(mod_data_dir) / RUN_MANIFEST_FILE
    atomic_write_json(manifest_path, payload)
    return manifest_path


def _count_removed(counts: dict[str, int], kind: str) -> None:
    counts[kind] = counts.get(kind, 0) + 1


def clear_run_state(mod_data_dir: str | Path) -> dict[str, int]:
    """Remove every per-run protocol file from the mod data dir.

    Returns counts by file kind so thousands of action files do not flood
    the runner log. Called before a run (so stale state cannot leak in)
    and after it (so the mod returns to idle).
    """
    data_dir = Path(mod_data_dir)
    counts: dict[str, int] = {}
    named = {
        RUN_MANIFEST_FILE: "run_manifest",
        TIMELINE_FILE: "timeline",
        ARMED_FLAG_FILE: "armed_flag",
        SUBSCRIPTION_FILE: "subscription",
        BLOCK_TABLE_FILE: "block_table",
        CONTROL_PROTOCOL_ERROR_FILE: "protocol_error",
        PROTOCOL_BENCHMARK_FLAG_FILE: "benchmark_flag",
        TELEMETRY_PUBLISH_FILE: "telemetry_marker",
        BULK_PUBLISH_FILE: "bulk_marker",
        CAMERA_POSE_FILE: "camera_pose",
    }
    for name, kind in named.items():
        path = data_dir / name
        if path.exists():
            path.unlink()
            _count_removed(counts, kind)
    for name in (*TELEMETRY_BUFFER_FILES, *BULK_BUFFER_FILES):
        path = data_dir / name
        if path.exists():
            path.unlink()
            _count_removed(counts, name.split("_", 1)[0])
    if data_dir.is_dir():
        for path in data_dir.glob(f"{ACTION_FILE_PREFIX}*{ACTION_FILE_SUFFIX}"):
            if parse_action_file_name(path.name) is not None:
                path.unlink()
                _count_removed(counts, "action")
        for path in data_dir.glob(f"{ACTION_FILE_PREFIX}*{ACTION_FILE_SUFFIX}.tmp"):
            path.unlink()
            _count_removed(counts, "action_tmp")
    for name in LEGACY_PROTOCOL_FILES:
        path = data_dir / name
        if path.exists():
            path.unlink()
            _count_removed(counts, "legacy")
        tmp = data_dir / f"{name}.tmp"
        if tmp.exists():
            tmp.unlink()
            _count_removed(counts, "legacy_tmp")
    return counts


def purge_mod_data(mod_data_dir: str | Path, *, recordings: bool = True) -> dict[str, int]:
    """Delete leftover protocol, export, and recorder files from the live data dir.

    This is an explicit wipe, not a migrate. Merged run outputs in
    datacache/ are not touched. Call only when Besiege is not running.
    """
    data_dir = Path(mod_data_dir)
    counts = clear_run_state(data_dir)
    if not data_dir.is_dir():
        return counts
    for name in LEFTOVER_EXPORT_FILES:
        path = data_dir / name
        if path.exists():
            path.unlink()
            _count_removed(counts, "leftover")
        tmp = data_dir / f"{name}.tmp"
        if tmp.exists():
            tmp.unlink()
            _count_removed(counts, "leftover_tmp")
    for path in data_dir.glob("*.bsg"):
        path.unlink()
        _count_removed(counts, "installed_machine")
    if recordings:
        for pattern in RECORDING_PURGE_GLOBS:
            for path in data_dir.glob(pattern):
                path.unlink()
                _count_removed(counts, "recording")
    return counts
