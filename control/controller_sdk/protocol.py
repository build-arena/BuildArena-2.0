"""File-protocol primitives and schema constants shared by the unified
besiege_cli runner, the Controller SDK, and (by contract) the
BuildArenaToolKit mod.

v4 is the only production protocol. v3 and older inputs are rejected.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from collections.abc import Iterable
from typing import Any

# --- BuildArenaToolKit mod identity (single source of truth) ---------------
TOOLKIT_MOD_ID = "9f0b7f40-7a36-48b7-8b18-7e0fbb5a4e00"
TOOLKIT_MOD_NAME = "BuildArena ToolKit"
TOOLKIT_MOD_VERSION = "2.0.9"
TOOLKIT_MOD_ENTRY = f"{TOOLKIT_MOD_ID}~L~{TOOLKIT_MOD_VERSION}~{TOOLKIT_MOD_NAME}"
TOOLKIT_DATA_DIR_NAME = f"BuildArenaToolKit_{TOOLKIT_MOD_ID}"
TOOLKIT_WORKSHOP_ITEM_ID = "3795335349"
BESIEGE_DLC_APP_IDS = (
    ("2165710", "The Splintered Sea", "water"),
    ("3639470", "The Broken Beyond", "space-flight"),
)
SETUP_REPORT_SCHEMA = "buildarena.setup_report.v1"
SETUP_REPORT_FILE = "setup-report.json"

# Retired identities: production code never migrates these. Preflight uses
# this reject list to fail old assets with an explicit error.
REJECTED_MOD_IDS = (
    "1d45bae7-50b4-4137-963e-27e45f6ece74",  # Block Tracker
    "855ab186-2795-434e-80aa-fec848b649b3",  # Block Inspector
)
REJECTED_MOD_NAMES = (
    "BuildArena Block Tracker",
    "BuildArena Block Inspector",
    "BuildArena Controller",
)
REJECTED_TOOLKIT_VERSIONS = ("1.0.0", "1.1.0", "1.2.0", "2.0.7", "2.0.8")
REJECTED_PROTOCOL_MAGICS = (b"BAA3", b"BAQ3", b"BAT3", b"BTM3", b"BAB3", b"BBM3")
REJECTED_SCHEMAS = (
    "buildarena.control_subscription.v3",
    "buildarena.block_table.v3",
    "buildarena.control_protocol_error.v3",
    "buildarena.live_action.v3",
    "buildarena.telemetry_sample.v3",
    "buildarena.telemetry_recording.v1",
    "buildarena.control_timeline.v1",
)
LEGACY_TRACKER_KEY_PREFIX = "tracker."

# --- Data-plane rates -------------------------------------------------------
TELEMETRY_SAMPLE_RATE_HZ = 25.0
LIVE_CONTROL_RATE_HZ = 50.0
DEFAULT_TELEMETRY_PROFILE = "full"
ACTION_TRANSPORT = "immutable_sequence_files"

# --- Schemas ----------------------------------------------------------------
PROTOCOL_VERSION = 4
TELEMETRY_MAGIC = b"BAT4"
TELEMETRY_MARKER_MAGIC = b"BTM4"
ACTION_MAGIC = b"BAA4"
BULK_MAGIC = b"BAB4"
BULK_MARKER_MAGIC = b"BBM4"
BULK_MAX_BATCH_SAMPLES = 100
RUN_MANIFEST_SCHEMA = "buildarena.run_manifest.v1"
RUN_STATUS_SCHEMA = "buildarena.run_status.v1"
RUN_SUMMARY_SCHEMA = "buildarena.run_summary.v1"
TIMELINE_SCHEMA = "buildarena.control_timeline.v2"
SUBSCRIPTION_SCHEMA = "buildarena.control_subscription.v4"
CONTROL_SUBSCRIPTION_SCHEMA = SUBSCRIPTION_SCHEMA
BLOCK_TABLE_SCHEMA = "buildarena.block_table.v4"
CONTROL_PROTOCOL_ERROR_SCHEMA = "buildarena.control_protocol_error.v4"
TELEMETRY_RECORDING_SCHEMA = "buildarena.telemetry_recording.v2"
TELEMETRY_RECORDER_ERROR_SCHEMA = "buildarena.telemetry_recorder_error.v1"
INSPECTOR_REQUEST_SCHEMA = "buildarena.inspector_request.v1"
INSPECTOR_REPORT_SCHEMA = "buildarena.inspector_report.v1"

# --- File names -------------------------------------------------------------
RUN_MANIFEST_FILE = "run_manifest.json"
TIMELINE_FILE = "control_timeline.json"
SUBSCRIPTION_FILE = "control_subscription.json"
CONTROL_SUBSCRIPTION_FILE = SUBSCRIPTION_FILE
BLOCK_TABLE_FILE = "block_table.json"
ACTION_FILE_PREFIX = "action_"
ACTION_FILE_SUFFIX = ".bin"
TELEMETRY_BUFFER_FILES = ("telemetry_0.bin", "telemetry_1.bin")
TELEMETRY_0_FILE, TELEMETRY_1_FILE = TELEMETRY_BUFFER_FILES
TELEMETRY_PUBLISH_FILE = "tm"
BULK_BUFFER_FILES = ("bulk_0.bin", "bulk_1.bin")
BULK_PUBLISH_FILE = "bm"
CONTROL_PROTOCOL_ERROR_FILE = "control_protocol_error.json"
PROTOCOL_BENCHMARK_FLAG_FILE = "protocol_benchmark.flag"
PROTOCOL_BENCHMARK_REPORT_FILE = "protocol_benchmark_report.json"
ARMED_FLAG_FILE = "live_control_armed.flag"
CAMERA_POSE_FILE = "camera_pose.txt"
INSPECTOR_REQUEST_FILE = "inspector_request.json"
INSPECTOR_REPORT_FILE = "inspector_report.json"
CHANNEL_CATALOG_FILE = "block_channel_catalog.json"
TELEMETRY_RECORDER_ERROR_FILE = "telemetry_recorder_error.json"
LEGACY_PROTOCOL_FILES = (
    "aq",
    "action.bin",
    "live_state.json",
    "live_track_guids.txt",
    "control_timeline.csv",
    "telemetry_sample.json",
)

# --- Machine-data keys ------------------------------------------------------
TELEMETRY_ENABLED_KEY = "telemetry.enabled"
TELEMETRY_TARGET_GUIDS_KEY = "telemetry.target_guids"
TELEMETRY_ALL_TARGETS_TOKEN = "*"
TELEMETRY_SAMPLE_RATE_KEY = "telemetry.sample_rate_hz"
TELEMETRY_OUTPUT_BASENAME_KEY = "telemetry.output_basename"
TELEMETRY_PROFILE_KEY = "telemetry.profile"
TELEMETRY_TARGET_FIELDS_KEY = "telemetry.target_fields"
TELEMETRY_MACHINE_FIELDS_KEY = "telemetry.machine_fields"
TELEMETRY_SOURCE_SHA256_KEY = "telemetry.source_sha256"
TELEMETRY_MACHINE_SHA256_KEY = "telemetry.machine_sha256"
TELEMETRY_RUN_ID_KEY = "telemetry.run_id"
TELEMETRY_MANAGED_KEY = "telemetry.managed"

ENV_RUN_ID = "BUILDARENA_RUN_ID"
ENV_MOD_DATA_DIR = "BUILDARENA_MOD_DATA_DIR"
ENV_MACHINE_BSG = "BUILDARENA_MACHINE_BSG"
ENV_CHANNEL_CATALOG = "BUILDARENA_CHANNEL_CATALOG"
ENV_RUN_DIR = "BUILDARENA_RUN_DIR"


def action_file_name(sequence: int) -> str:
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise TypeError("sequence must be an integer.")
    if sequence < 1:
        raise ValueError("action sequence must be >= 1.")
    return f"{ACTION_FILE_PREFIX}{sequence}{ACTION_FILE_SUFFIX}"


def parse_action_file_name(name: str) -> int | None:
    if not name.startswith(ACTION_FILE_PREFIX) or not name.endswith(ACTION_FILE_SUFFIX):
        return None
    body = name[len(ACTION_FILE_PREFIX) : -len(ACTION_FILE_SUFFIX)]
    if not body.isdigit():
        return None
    sequence = int(body)
    if sequence < 1:
        return None
    return sequence


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    durable: bool = False,
    attempts: int = 20,
    delay: float = 0.01,
    exist_ok: bool = True,
) -> None:
    """Atomically publish bytes; hot-path durability is opt-in.

    ``exist_ok=False`` refuses to replace an existing path. Immutable action
    files use that mode so a sequence is never overwritten.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not exist_ok and path.exists():
        raise RuntimeError(f"Refusing to overwrite existing file {path}.")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        if durable:
            os.fsync(stream.fileno())
    error: OSError | None = None
    for attempt in range(attempts):
        try:
            if not exist_ok and path.exists():
                temporary.unlink(missing_ok=True)
                raise RuntimeError(f"Refusing to overwrite existing file {path}.")
            os.replace(temporary, path)
            return
        except RuntimeError:
            raise
        except OSError as exc:
            error = exc
            time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"Could not atomically replace {path}: {error}")


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    attempts: int = 20,
    delay: float = 0.01,
    *,
    durable: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
        stream.flush()
        if durable:
            os.fsync(stream.fileno())
    error: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            error = exc
            time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"Could not atomically replace {path}: {error}")


def normalize_telemetry_target_guids(guids: Iterable[str] | None) -> tuple[str, ...]:
    """Return specific GUIDs, or ``()`` meaning every simulation block.

    ``None``, an empty list, or the single token ``*`` all mean the full
    machine. ``*`` mixed with concrete GUIDs is an error.
    """
    if guids is None:
        return ()
    items = [str(guid).strip() for guid in guids]
    if any(not item for item in items):
        raise ValueError("target_guids must contain only non-empty GUID strings.")
    if not items or items == [TELEMETRY_ALL_TARGETS_TOKEN]:
        return ()
    if TELEMETRY_ALL_TARGETS_TOKEN in items:
        raise ValueError("'*' cannot be mixed with specific target GUIDs.")
    unique = tuple(dict.fromkeys(items))
    if len(unique) != len(items):
        raise ValueError("target_guids must not contain duplicates.")
    return unique


def read_shared_bytes(path: Path) -> bytes:
    """Read a file without taking an exclusive Windows lock.

    CPython ``Path.read_bytes()`` uses ``_wopen`` with ``_SH_DENYRW`` on
    Windows. That exclusive reader blocks ``ModIO.WriteAllBytes``
    (``FileShare.None``) on ``tm`` / ``telemetry_*.bin`` / ``bm`` and
    produces Sharing violation in the game log. This open allows other
    readers and writers; torn reads are rejected by the BAT4/BTM4 commit
    protocol, not here.
    """
    path = Path(path)
    if os.name != "nt":
        return path.read_bytes()

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_attribute_normal = 0x80
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        str(path),
        generic_read,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        raise OSError(error, f"shared read failed for {path}", str(path), error)
    fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
    with os.fdopen(fd, "rb", closefd=True) as stream:
        return stream.read()


def read_json_retry(path: Path, attempts: int = 5, delay: float = 0.02) -> dict[str, Any]:
    error: Exception | None = None
    for _ in range(attempts):
        try:
            with path.open("r", encoding="utf-8-sig") as stream:
                value = json.load(stream)
            if not isinstance(value, dict):
                raise ValueError(f"Expected a JSON object in {path}")
            return value
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            error = exc
            time.sleep(delay)
    raise RuntimeError(f"Could not read a complete state from {path}: {error}")
