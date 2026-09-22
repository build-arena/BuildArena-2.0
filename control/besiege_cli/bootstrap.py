"""Staged one-command setup: detect, enable, inspect, build, and smoke-test."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from controller_sdk.protocol import (
    SETUP_REPORT_FILE,
    SETUP_REPORT_SCHEMA,
    TOOLKIT_MOD_ID,
    TOOLKIT_MOD_NAME,
    atomic_write_json,
)
from blocks.control_descriptors.keylist_bindings import KEYLIST_BINDINGS

from .compat import CompatibilityError, verify_compatibility
from .channel_catalog import assemble_channel_catalog
from .inspector import (
    BEHAVIOUR_TYPES_NAME,
    CHANNEL_CATALOG_NAME,
    InspectorError,
    collect_inspector_artifacts,
    copy_inspector_artifacts,
    existing_collider_dump,
    inspector_dump_reusable,
    load_catalog_block_ids,
    wait_for_inspector_report,
    write_inspector_request,
)
from .machine import parse_bsg
from .modding import ModdingConfigError, enable_toolkit_mod
from .orchestrator import BesiegeOrchestrator, OrchestratorTimeoutError
from .paths import datacache_dir, mod_data_dir
from .preflight import PreflightError
from .run import RunFailedError, cmd_run
from .session import (
    DEFAULT_SANDBOX_LEVEL,
    ensure_fresh_sandbox,
    ensure_game,
    ensure_sandbox,
    quit_game,
)
from .steam import (
    detect_besiege_data,
    dlc_manifest_status,
    normalize_besiege_data,
    open_workshop_page,
    workshop_item_status,
)

TOOLKIT_SOURCE_WORKSHOP = "workshop"
LAUNCHER_EXAMPLE_JSON = Path("control") / "examples" / "rocket_orbit_return" / "machine.json"
LAUNCHER_EVIDENCE_NAME = "mission_summary.json"
LAUNCHER_SANDBOX = "LONE ORB"
SMOKE_HOLD_SECONDS = 15.0
# Wall-clock budget for that simulation hold. Windows and Linux finish the
# all-block machine inside 60s. The macOS binary is x86_64; under Rosetta on
# Apple silicon the same 15s of simulation needs a longer wall-clock budget.
SMOKE_CONTROLLER_TIMEOUT_BY_SYSTEM = {
    "Windows": 60.0,
    "Linux": 60.0,
    "Darwin": 300.0,
}

# Platforms the one-command setup runs on. Windows is the reference desktop
# install; Linux is the headless server target; Darwin is the macOS desktop.
SUPPORTED_SYSTEMS = ("Windows", "Linux", "Darwin")

STAGE_PENDING = "pending"
STAGE_RUNNING = "running"
STAGE_PASSED = "passed"
STAGE_BLOCKED = "blocked"
STAGE_FAILED = "failed"

STATUS_PASSED = "passed"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"


class BootstrapBlocked(RuntimeError):
    """A human entitlement step is required (DLC or Workshop)."""


class BootstrapError(RuntimeError):
    """An automated setup stage failed."""


@dataclass
class Stage:
    name: str
    status: str = STAGE_PENDING
    detail: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class BootstrapReport:
    status: str = STAGE_PENDING
    toolkit_source: str = TOOLKIT_SOURCE_WORKSHOP
    unverified_distribution: str | None = None
    stages: list[Stage] = field(default_factory=list)
    game_ownership: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    def add(self, stage: Stage) -> Stage:
        self.stages.append(stage)
        return stage

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SETUP_REPORT_SCHEMA,
            "status": self.status,
            "toolkit_source": self.toolkit_source,
            "unverified_distribution": self.unverified_distribution,
            "game_ownership": self.game_ownership,
            "stages": [stage.to_dict() for stage in self.stages],
            **self.extras,
        }


def write_report(*, path: Path, report: BootstrapReport) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, report.to_dict())
    return path


SETUP_ENV_RELATIVE = {
    "BLOCK_REGISTRY_PATH": "blocks/block_registry.generated.toml",
    "BLOCK_ROLES_PATH": "blocks/block_roles.toml",
    "BLOCK_AUTHORING_PATH": "blocks/block_authoring.toml",
    "COLLIDER_DUMP_PATH": ".local/collider_dump.toml",
}


def write_setup_env(
    *,
    repo_root: Path,
    besiege_data: Path,
    saved_machine_dir: Path,
) -> Path:
    """Write a complete ``.env`` with repo-relative files and detected game paths."""
    env_path = repo_root / ".env"
    lines = [
        "# Written by scripts/setup.py with detected local paths. Do not commit.",
        "",
        "# Project data files. Relative paths are resolved from the repository root.",
        f"BLOCK_REGISTRY_PATH={SETUP_ENV_RELATIVE['BLOCK_REGISTRY_PATH']}",
        f"BLOCK_ROLES_PATH={SETUP_ENV_RELATIVE['BLOCK_ROLES_PATH']}",
        f"BLOCK_AUTHORING_PATH={SETUP_ENV_RELATIVE['BLOCK_AUTHORING_PATH']}",
        f"COLLIDER_DUMP_PATH={SETUP_ENV_RELATIVE['COLLIDER_DUMP_PATH']}",
        "",
        "# Besiege_Data must be the Unity data directory, not the Besiege install root.",
        f"BESIEGE_DATA_PATH={besiege_data}",
        f"SAVED_MACHINE_DIR={saved_machine_dir}",
        "",
        "# Subscribe to BuildArena ToolKit, then re-run scripts/setup.py if needed:",
        "# https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path


def reload_setup_env(*, env_path: Path) -> None:
    import os

    from buildarena.paths import load_project_env

    # Retired: catalog now lives in the ToolKit data dir under BESIEGE_DATA_PATH.
    os.environ.pop("BLOCK_CHANNEL_CATALOG", None)
    load_project_env(env_path=env_path, overwrite=True)


def write_mcp_json(*, repo_root: Path) -> Path:
    mcp_path = repo_root / "mcp.json"
    payload = {
        "mcpServers": {
            "build-arena": {
                "command": "uv",
                "args": [
                    "run",
                    "--directory",
                    str(repo_root),
                    "python",
                    "-m",
                    "buildarena.mcp_server",
                ],
            }
        }
    }
    mcp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return mcp_path


def _missing_dlc_categories(*, dumped_ids: tuple[int, ...], expected: dict[str, tuple[int, ...]]) -> dict[str, list[int]]:
    dumped = set(dumped_ids)
    missing: dict[str, list[int]] = {}
    for category, ids in expected.items():
        absent = [block_id for block_id in ids if block_id not in dumped]
        if absent:
            missing[category] = absent
    return missing


def _smoke_namespace(
    *,
    bsg: Path,
    controller: Path,
    catalog: Path,
    run_dir: Path,
    besiege_data: Path,
    launch_timeout: float,
    timeout: float,
    experiment: str = "setup_smoke",
    sandbox: str = DEFAULT_SANDBOX_LEVEL,
    telemetry_hz: int = 25,
    controller_timeout: float = 120.0,
    camera_follow: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        besiege_data=str(besiege_data),
        poll_interval=0.25,
        timeout=timeout,
        bsg=str(bsg),
        controller=str(controller),
        sandbox=sandbox,
        catalog=str(catalog),
        run_dir=str(run_dir),
        experiment=experiment,
        track_guids=(),
        track_blocks=(),
        telemetry_hz=telemetry_hz,
        telemetry_profile="full",
        telemetry_fields=None,
        machine_fields=None,
        record=False,
        record_fps=25,
        camera_follow=camera_follow,
        camera_distance=None,
        camera_pitch=None,
        pre_controller_hold=0.0,
        pre_controller_hold_timeout=0.0,
        post_completion_hold=0.0,
        post_completion_hold_timeout=0.0,
        recorder_hz=25,
        no_recorder=False,
        bulk_hz=None,
        bulk_batch=None,
        protocol_benchmark=False,
        launch_timeout=launch_timeout,
        playback_timeout=120.0,
        controller_timeout=controller_timeout,
    )


def _validate_smoke_outputs(
    *,
    run_dir: Path,
    expected_guids: set[str],
) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        raise BootstrapError(f"Smoke run did not write {summary_path}.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("result") != "ok":
        raise BootstrapError(f"Smoke summary.result is {summary.get('result')!r}, expected 'ok'.")
    evidence_path = run_dir / "smoke_evidence.json"
    if not evidence_path.is_file():
        raise BootstrapError(f"Smoke controller did not write {evidence_path}.")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    sampled = {str(guid).lower() for guid in evidence.get("sampled_guids", [])}
    table = {str(guid).lower() for guid in evidence.get("block_table_guids", [])}
    expected_lower = {guid.lower() for guid in expected_guids}
    if table != expected_lower:
        raise BootstrapError(
            f"block_table GUIDs {sorted(table)} do not match prepared machine GUIDs {sorted(expected_lower)}."
        )
    if not sampled:
        raise BootstrapError("Smoke collected no live (valid=1) telemetry GUIDs.")
    unexpected = sorted(sampled - expected_lower)
    if unexpected:
        raise BootstrapError(
            f"Telemetry sampled GUIDs not present on the prepared machine: {unexpected}."
        )
    sequences = evidence.get("sequences") or []
    if len(sequences) < 5:
        raise BootstrapError(f"Smoke collected {len(sequences)} BAT4 frames; need at least 5.")
    hold_elapsed = evidence.get("hold_elapsed")
    if not isinstance(hold_elapsed, (int, float)) or hold_elapsed + 1e-6 < SMOKE_HOLD_SECONDS:
        raise BootstrapError(
            f"Smoke hold_elapsed is {hold_elapsed!r}s; needed {SMOKE_HOLD_SECONDS:.1f}s of "
            f"simulation time (stop_reason={evidence.get('stop_reason')!r})."
        )
    recorder_csv = run_dir / "telemetry.csv"
    if not recorder_csv.is_file():
        raise BootstrapError(f"Telemetry recorder did not finalize {recorder_csv}.")
    return {
        "summary": str(summary_path),
        "evidence": str(evidence_path),
        "telemetry_csv": str(recorder_csv),
        "sampled_guid_count": len(sampled),
        "hold_elapsed": hold_elapsed,
        "stop_reason": evidence.get("stop_reason"),
        "blocks_lost": evidence.get("blocks_lost"),
        "omitted_guid_count": len(evidence.get("omitted_guids") or []),
        "sequences": sequences,
    }


def _run_rebuild_script(
    *,
    repo_root: Path,
    record_json: Path,
    machine_name: str,
) -> Path:
    script = repo_root / "scripts" / "rebuild_from_record.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--record-json",
            str(record_json),
            "--machine-name",
            machine_name,
            "--replace",
        ],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "<no output>"
        raise BootstrapError(
            f"scripts/rebuild_from_record.py exited {completed.returncode}: {detail}"
        )
    bsg_path: Path | None = None
    for line in completed.stdout.splitlines():
        if line.startswith("bsg_path="):
            bsg_path = Path(line.split("=", 1)[1].strip())
    if bsg_path is None or not bsg_path.is_file():
        raise BootstrapError(
            "scripts/rebuild_from_record.py did not report a written BSG path.\n"
            f"stdout:\n{completed.stdout}"
        )
    return bsg_path


def _validate_launcher_demo_outputs(
    *,
    run_dir: Path,
    expected_guids: set[str],
) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        raise BootstrapError(f"Launcher demo did not write {summary_path}.")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("result") != "ok":
        raise BootstrapError(
            f"Launcher demo summary.result is {summary.get('result')!r}, expected 'ok'."
        )
    manifest_path = run_dir / "input_manifest.json"
    if not manifest_path.is_file():
        raise BootstrapError(f"Launcher demo did not write {manifest_path}.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sandbox = manifest.get("sandbox")
    if sandbox != LAUNCHER_SANDBOX:
        raise BootstrapError(
            f"Launcher demo sandbox is {sandbox!r}, expected {LAUNCHER_SANDBOX!r}."
        )
    evidence_path = run_dir / LAUNCHER_EVIDENCE_NAME
    if not evidence_path.is_file():
        raise BootstrapError(f"Launcher controller did not write {evidence_path}.")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schema") != "buildarena.launcher_demo_evidence.v1":
        raise BootstrapError(
            f"Launcher evidence schema is {evidence.get('schema')!r}, expected "
            "buildarena.launcher_demo_evidence.v1."
        )
    hardware = evidence.get("hardware") or {}
    engine_guids = [str(guid).lower() for guid in hardware.get("engine_guids") or []]
    expected_lower = {guid.lower() for guid in expected_guids}
    if len(engine_guids) < 4:
        raise BootstrapError(
            f"Launcher demo discovered {len(engine_guids)} Booster GUIDs; need at least 4."
        )
    unexpected = sorted(set(engine_guids) - expected_lower)
    if unexpected:
        raise BootstrapError(
            f"Launcher engine GUIDs not present on the rebuilt machine: {unexpected}."
        )
    final_stage = evidence.get("final_stage")
    if final_stage in (None, "", "pad"):
        raise BootstrapError(
            f"Launcher mission never left the pad (final_stage={final_stage!r})."
        )
    step_count = evidence.get("steps")
    if not isinstance(step_count, int) or step_count < 50:
        raise BootstrapError(
            f"Launcher demo wrote {step_count!r} closed-loop steps; need at least 50."
        )
    steps_path = run_dir / "steps.jsonl"
    if not steps_path.is_file():
        raise BootstrapError(f"Launcher controller did not write {steps_path}.")
    recorder_csv = run_dir / "telemetry.csv"
    if not recorder_csv.is_file():
        raise BootstrapError(f"Telemetry recorder did not finalize {recorder_csv}.")
    return {
        "summary": str(summary_path),
        "evidence": str(evidence_path),
        "telemetry_csv": str(recorder_csv),
        "sandbox": sandbox,
        "final_stage": final_stage,
        "abort_reason": evidence.get("abort_reason"),
        "steps": step_count,
        "engine_guid_count": len(engine_guids),
    }


KEYLIST_CACHE_NAME = "block_live_keylist_cache.tsv"
PRIME_SIM_HOLD_SECONDS = 4.0


def _keylist_cache_rows(cache_path: Path) -> dict[int, list[str]]:
    """Parse ``block_live_keylist_cache.tsv`` into {block_id: [channel, ...]}.

    Each non-empty line is ``<block_id>\\t<behaviour>\\t<channel,channel,...>``,
    or just ``<block_id>\\t<behaviour>`` when the block exposes zero channels.
    Malformed lines are skipped; a missing file yields an empty mapping.
    """
    rows: dict[int, list[str]] = {}
    if not cache_path.is_file():
        return rows
    for raw in cache_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        # Strip only to test for a blank line. Splitting the unstripped line
        # preserves a trailing tab, which marks a third (empty) field and so a
        # real empty KeyList slot, distinct from the two-column zero-channel form.
        if not raw.strip():
            continue
        parts = raw.split("\t")
        try:
            block_id = int(parts[0])
        except (ValueError, IndexError):
            continue
        channels: list[str] = []
        # Preserve every field, including empty strings: an empty KeyList
        # value is still a real slot (assemble_channel_catalog keeps these
        # entries for exactly this reason), so `key0,,key2` must stay three
        # slots rather than being compressed to two. Zero channels are
        # expressed by the two-column form (no third field).
        if len(parts) >= 3:
            channels = [item.strip() for item in parts[2].split(",")]
        rows[block_id] = channels
    return rows


# Connector blocks placed via connect_blocks() in build_validation_machine,
# which appear in the loaded machine but are not in inspect_registry's
# render_blocks (type=connection entries are excluded there). Keeping the
# prime requirement in sync with the builder avoids demanding coverage for a
# connection the machine never wires up.
_PRIME_CONNECTED_BLOCK_IDS = {7, 9, 45, 75, 96}  # Brace, Spring, Rope Winch, Rope Measure, Fuel Line


def _keylist_cache_needed_blocks() -> dict[int, tuple[list[int], set[int]]]:
    """For every block the validation machine loads and whose catalog slots
    the assembly validates, return ``(bound_slots, ignored_channel_indices)``.

    Mirrors ``assemble_channel_catalog``: a block needs live KeyList coverage
    when it declares real binding slots, or declares ``channel_N`` ignored
    entries that a prefab-only pass cannot observe. Blocks with neither
    require nothing from the cache.

    The candidate set is exactly what ``build_validation_machine`` places:
    ``inspect_registry().render_blocks`` (which already excludes disabled,
    EXCLUDE_IDS, unplaceable, and un-``connect``ed connection blocks) plus the
    connectors wired via ``connect_blocks`` plus the Starting Block pads. This
    keeps the requirement aligned with what is actually loaded and run, so a
    block the builder cannot capture is never demanded here.
    """
    from buildarena.control_descriptor_loader import load_control_semantics
    from buildarena.validation_machine import inspect_registry

    placed = {inspection.block_id for inspection in inspect_registry().render_blocks}
    placed |= _PRIME_CONNECTED_BLOCK_IDS
    placed.add(0)  # Starting Block pads

    needed: dict[int, tuple[list[int], set[int]]] = {}
    for block_id in placed:
        if block_id not in KEYLIST_BINDINGS:
            continue
        semantics = load_control_semantics(block_id=block_id)
        if semantics is None:
            continue
        bound = sorted(semantics.keylist_indices.values())
        ignored = {
            int(name[8:]) for name in semantics.ignored if name.startswith("channel_")
        }
        if bound or ignored:
            needed[block_id] = (bound, ignored)
    return needed


def _keylist_uncovered_block_ids(
    *, rows: dict[int, list[str]], needed: dict[int, tuple[list[int], set[int]]]
) -> list[int]:
    """Block IDs whose observed KeyList slots do not match their declared slots.

    Mirrors ``assemble_channel_catalog`` exactly: a block is covered when its
    observed slot set (``range(len(cached))``) equals the set of declared
    binding slots plus ignored ``channel_N`` indices, or when an empty cache
    matches an ignored-only declaration (the Starting Block's inert KeyList).
    This is stricter than a minimum-count check: an extra native slot is a
    mismatch that would fail catalog assembly, so it must not count as covered.
    """
    gaps: list[int] = []
    for block_id, (bound, ignored) in needed.items():
        cached = rows.get(block_id, ())
        expected = set(bound) | ignored
        observed = set(range(len(cached)))
        if expected != observed and not (not cached and expected == ignored):
            gaps.append(block_id)
    return gaps


def keylist_cache_complete(*, cache_path: Path) -> dict[int, list[str]]:
    """Return the parsed cache when every validated block is covered, else ``{}``.

    Coverage uses the same exact-slot condition as ``assemble_channel_catalog``,
    so a cache accepted here cannot later fail catalog assembly.
    """
    rows = _keylist_cache_rows(cache_path)
    needed = _keylist_cache_needed_blocks()
    if _keylist_uncovered_block_ids(rows=rows, needed=needed):
        return {}
    return rows


def _prime_keylist_cache(
    *,
    orchestrator: BesiegeOrchestrator,
    saved_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    """Load the assembled validation machine, run it briefly, and stop, so the
    ToolKit populates its live KeyList cache for every supported block."""
    from buildarena.validation_machine import build_validation_machine, inspect_registry

    inspection = inspect_registry()
    machine = build_validation_machine(output_dir=saved_dir, inspection=inspection)
    # ensure_fresh_sandbox, not ensure_sandbox(force=True): re-sending
    # enter_sandbox for a scene already loaded returns immediately on the
    # stale heartbeat, so load_machine right after could race the reload.
    ensure_fresh_sandbox(orchestrator=orchestrator, timeout=timeout)
    installed_name = orchestrator.install_machine(
        source_bsg=machine.bsg_path, name="assembled_validation.bsg"
    )
    load_seq = orchestrator.send_command("load_machine", path=installed_name)
    orchestrator.wait_for_command_result(load_seq, timeout=timeout)
    start_requested = False
    try:
        start_seq = orchestrator.send_command("start_sim")
        # The game may start even if its acknowledgement is delayed or lost.
        # Arrange cleanup as soon as the command has been sent, before waiting.
        start_requested = True
        orchestrator.wait_for_command_result(start_seq, timeout=timeout)
        orchestrator.wait_for_simulating(True, timeout=timeout)
        end = time.monotonic() + PRIME_SIM_HOLD_SECONDS
        while time.monotonic() < end:
            time.sleep(0.25)
    finally:
        # Always stop the simulation so a timed-out prime does not leave
        # Besiege simulating and affect the next setup/run.
        if start_requested:
            stop_seq = orchestrator.send_command("stop_sim")
            orchestrator.wait_for_command_result(stop_seq, timeout=timeout)
            orchestrator.wait_for_simulating(False, timeout=timeout)
    return {
        "bsg": str(machine.bsg_path),
        "saved_machine_dir": str(saved_dir),
        "hold_seconds": PRIME_SIM_HOLD_SECONDS,
    }


def run_bootstrap(
    *,
    repo_root: Path,
    besiege_data_override: str | None = None,
    interactive: bool = True,
    launch_timeout: float = 120.0,
    inspector_timeout: float = 300.0,
    skip_inspector_request_if_complete: bool = True,
) -> BootstrapReport:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    report = BootstrapReport(toolkit_source=TOOLKIT_SOURCE_WORKSHOP)
    report_path = repo_root / ".local" / SETUP_REPORT_FILE

    def persist() -> None:
        write_report(path=report_path, report=report)

    try:
        system = platform.system()
        if system not in SUPPORTED_SYSTEMS:
            raise BootstrapError(
                f"BuildArena setup supports {', '.join(SUPPORTED_SYSTEMS)}. "
                f"Detected platform: {system}."
            )
        report.add(Stage(name="platform", status=STAGE_PASSED, message=f"{system} host."))

        if besiege_data_override is not None:
            besiege_data = normalize_besiege_data(raw=besiege_data_override)
            probe = "flag"
        else:
            found = detect_besiege_data()
            if found is None:
                raise BootstrapBlocked(
                    "Besiege_Data was not found. Install Steam, buy/install Besiege and both DLC "
                    "(The Splintered Sea, The Broken Beyond), then re-run. If the game is on a "
                    "non-default drive, pass --besiege-data."
                )
            besiege_data = found
            probe = "detected"
        saved_dir = besiege_data / "SavedMachines" / "BuildArena"
        saved_dir.mkdir(parents=True, exist_ok=True)
        env_path = write_setup_env(
            repo_root=repo_root,
            besiege_data=besiege_data,
            saved_machine_dir=saved_dir,
        )
        reload_setup_env(env_path=env_path)
        report.add(
            Stage(
                name="env",
                status=STAGE_PASSED,
                message=str(env_path),
                detail={
                    "path": str(env_path),
                    "besiege_data": str(besiege_data),
                    "saved_machine_dir": str(saved_dir),
                    "reloaded": True,
                },
            )
        )
        report.add(
            Stage(
                name="steam_besiege",
                status=STAGE_PASSED,
                message=f"Besiege_Data via {probe}: {besiege_data}",
                detail={"besiege_data": str(besiege_data), "probe": probe, "saved_machine_dir": str(saved_dir)},
            )
        )

        workshop = workshop_item_status(besiege_data=besiege_data)
        if not workshop.installed:
            if interactive and workshop.configured:
                open_workshop_page(item_id=workshop.item_id)
            raise BootstrapBlocked(
                f"{TOOLKIT_MOD_NAME} Workshop item is not available. {workshop.reason} "
                f"Subscribe at {workshop.workshop_url} then re-run."
            )
        report.add(
            Stage(
                name="toolkit_source",
                status=STAGE_PASSED,
                message=f"ToolKit Workshop item {workshop.item_id} is installed.",
                detail={
                    "source": TOOLKIT_SOURCE_WORKSHOP,
                    "item_id": workshop.item_id,
                    "content_dir": str(workshop.content_dir) if workshop.content_dir else None,
                    "workshop_url": workshop.workshop_url,
                },
            )
        )

        enable = enable_toolkit_mod(besiege_data=besiege_data)
        report.add(
            Stage(
                name="enable_mod",
                status=STAGE_PASSED,
                message=(
                    f"{TOOLKIT_MOD_NAME} already enabled."
                    if enable.already_enabled
                    else f"Removed {TOOLKIT_MOD_ID} from disabled-mods."
                ),
                detail={
                    "path": str(enable.path),
                    "backup": str(enable.backup_path) if enable.backup_path else None,
                    "changed": enable.changed,
                    "already_enabled": enable.already_enabled,
                },
            )
        )

        compat = verify_compatibility(besiege_data)
        if compat:
            report.add(
                Stage(
                    name="compatibility",
                    status=STAGE_PASSED,
                    message=compat,
                    detail={"notice": compat},
                )
            )

        data_dir = mod_data_dir(besiege_data)
        orchestrator = BesiegeOrchestrator(data_dir)
        session = ensure_game(
            orchestrator=orchestrator, besiege_data=besiege_data, timeout=launch_timeout
        )
        report.game_ownership = {
            "already_running": session.already_running,
            "launched_by_setup": session.launched,
            "quit_after": None,
        }
        report.add(
            Stage(
                name="launch",
                status=STAGE_PASSED,
                message="already running" if session.already_running else "launched by setup",
                detail=report.game_ownership,
            )
        )

        from buildarena.validation_machine import inspect_dlc_block_ids

        dlc_block_ids = inspect_dlc_block_ids()
        dump_report = existing_collider_dump(data_dir=data_dir)
        complete = False
        if (
            skip_inspector_request_if_complete
            and inspector_dump_reusable(report=dump_report)
        ):
            if dump_report is None:
                raise BootstrapError("Inspector dump was marked reusable but no report was loaded.")
            missing = _missing_dlc_categories(
                dumped_ids=dump_report.dumped_block_ids,
                expected=dlc_block_ids,
            )
            complete = not missing
        if complete and dump_report is not None:
            inspector_report = dump_report
            inspector_mode = "reused_existing_artifacts"
        else:
            request_id = write_inspector_request(data_dir=data_dir)
            print(
                f"Wrote Inspector request {request_id}; re-entering sandbox so "
                "ToolKit picks it up on scene load.",
                flush=True,
            )
            ensure_sandbox(
                orchestrator=orchestrator,
                timeout=launch_timeout,
                force=True,
            )
            payload = wait_for_inspector_report(
                data_dir=data_dir,
                request_id=request_id,
                timeout=inspector_timeout,
            )
            inspector_report = collect_inspector_artifacts(data_dir=data_dir, request_id=request_id, payload=payload)
            inspector_mode = "inspector_request"

        missing_dlc = _missing_dlc_categories(
            dumped_ids=inspector_report.dumped_block_ids,
            expected=dlc_block_ids,
        )
        dlc_manifests = [item.__dict__ for item in dlc_manifest_status(besiege_data=besiege_data)]
        if missing_dlc:
            raise BootstrapBlocked(
                "Inspector dump is missing DLC block IDs, so water/space content is not installed "
                f"or not available: {missing_dlc}. Buy and install The Splintered Sea (2165710) "
                "and The Broken Beyond (3639470), then re-run."
            )

        collider_dest = repo_root / ".local" / "collider_dump.toml"
        behaviour_dest = repo_root / ".local" / BEHAVIOUR_TYPES_NAME
        catalog_dest = data_dir / CHANNEL_CATALOG_NAME
        copied = copy_inspector_artifacts(
            report=inspector_report,
            collider_dest=collider_dest,
            behaviour_dest=behaviour_dest,
            catalog_dest=catalog_dest,
        )
        reload_setup_env(env_path=env_path)
        from buildarena.validation_machine import inspect_registry

        inspection = inspect_registry()
        report.add(
            Stage(
                name="inspector",
                status=STAGE_PASSED,
                message=inspector_mode,
                detail={
                    "mode": inspector_mode,
                    "request_id": inspector_report.request_id,
                    "dumped_block_count": len(inspector_report.dumped_block_ids),
                    "copied": copied,
                    "dlc_manifests": dlc_manifests,
                },
            )
        )
        report.add(
            Stage(
                name="dlc_coverage",
                status=STAGE_PASSED,
                message="Inspector dump covers water and space-flight registry IDs.",
                detail={"expected": {key: list(value) for key, value in dlc_block_ids.items()}},
            )
        )

        # KeyList priming runs here, after the Inspector has copied the collider
        # dump: inspecting the registry (to build the validation machine) reads
        # COLLIDER_DUMP_PATH, so it cannot run before this point on a clean
        # checkout. The cache is only consumed later, at catalog assembly.
        cache_path = data_dir / KEYLIST_CACHE_NAME
        cached = keylist_cache_complete(cache_path=cache_path)
        if cached:
            print(
                "KeyList cache is complete; skipping the load-and-run prime. "
                f"({len(cached)} block(s) covered)",
                flush=True,
            )
            report.add(
                Stage(
                    name="prime_keylist",
                    status=STAGE_PASSED,
                    message="complete",
                    detail={"mode": "cache_complete", "block_count": len(cached)},
                )
            )
        else:
            print(
                "KeyList cache is missing blocks. Loading the assembled validation "
                "machine and running it briefly to populate the ToolKit keylist cache.",
                flush=True,
            )
            prime_detail = _prime_keylist_cache(
                orchestrator=orchestrator,
                saved_dir=saved_dir,
                timeout=launch_timeout,
            )
            refreshed = keylist_cache_complete(cache_path=cache_path)
            if not refreshed:
                missing_detail = _keylist_uncovered_block_ids(
                    rows=_keylist_cache_rows(cache_path),
                    needed=_keylist_cache_needed_blocks(),
                )
                raise BootstrapError(
                    "KeyList cache is still incomplete after loading and running the "
                    f"validation machine; missing block IDs {sorted(set(missing_detail))}. "
                    "This may indicate a game-version or DLC-authorization difference. "
                    "Manually load the assembled_validation machine in Besiege, run it, "
                    "and re-run setup."
                )
            # The cache was empty during the first Inspector scan, so that
            # behaviour_types has stale (empty) key_list_channels. Re-scan now
            # that the cache is populated and refresh the file the catalog uses.
            request_id = write_inspector_request(data_dir=data_dir)
            ensure_fresh_sandbox(orchestrator=orchestrator, timeout=launch_timeout)
            payload = wait_for_inspector_report(
                data_dir=data_dir, request_id=request_id, timeout=inspector_timeout
            )
            inspector_report = collect_inspector_artifacts(
                data_dir=data_dir, request_id=request_id, payload=payload
            )
            copied = copy_inspector_artifacts(
                report=inspector_report,
                collider_dest=collider_dest,
                behaviour_dest=behaviour_dest,
                catalog_dest=catalog_dest,
            )
            report.add(
                Stage(
                    name="prime_keylist",
                    status=STAGE_PASSED,
                    message="primed",
                    detail={
                        "mode": "primed",
                        "rebuilt_bsg": prime_detail.get("bsg"),
                        "rescan_request_id": inspector_report.request_id,
                    },
                )
            )

        if not behaviour_dest.is_file():
            raise BootstrapError(
                f"{behaviour_dest} is missing, so the channel catalog cannot be "
                "assembled from Inspector artefacts."
            )
        assemble_channel_catalog(behaviour_path=behaviour_dest, dest=catalog_dest)
        catalog_ids = set(load_catalog_block_ids(catalog_path=catalog_dest))
        needed_ids = {item.block_id for item in inspection.render_blocks} | {0}
        absent = sorted(needed_ids - catalog_ids)
        if absent:
            raise BootstrapError(
                f"{catalog_dest} is missing validation-machine block IDs {absent}."
            )
        report.add(
            Stage(
                name="catalog",
                status=STAGE_PASSED,
                message="catalog_source=assembled_from_inspector",
                detail={
                    "path": str(catalog_dest),
                    "source": "assembled_from_inspector",
                    "block_count": len(catalog_ids),
                },
            )
        )

        from buildarena.validation_machine import build_validation_machine

        machine = build_validation_machine(output_dir=saved_dir, inspection=inspection)
        report.add(
            Stage(
                name="validation_machine",
                status=STAGE_PASSED,
                message=str(machine.bsg_path),
                detail={
                    "bsg": str(machine.bsg_path),
                    "history": str(machine.history_path),
                    "included_block_ids": list(machine.included_block_ids),
                    "excluded": [
                        {"block_id": item.block_id, "name": item.display_name, "reason": item.reason}
                        for item in machine.excluded
                    ],
                    "attached": list(machine.attached_names),
                },
            )
        )

        print(
            f"Starting assembled-validation smoke: {SMOKE_HOLD_SECONDS:.0f}s all-block hold "
            "on BARREN EXPANSE. Block loss is recorded, not treated as failure.",
            flush=True,
        )
        run_dir = datacache_dir() / "control_experiments" / "setup_smoke" / "latest"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        controller = Path(__file__).resolve().parent / "setup_smoke_controller.py"
        exit_code = cmd_run(
            _smoke_namespace(
                bsg=machine.bsg_path,
                controller=controller,
                catalog=catalog_dest,
                run_dir=run_dir,
                besiege_data=besiege_data,
                launch_timeout=launch_timeout,
                timeout=90.0,
                controller_timeout=SMOKE_CONTROLLER_TIMEOUT_BY_SYSTEM[system],
            )
        )
        if exit_code != 0:
            raise BootstrapError(f"besiege_cli run smoke exited {exit_code}. See {run_dir}.")
        _, prepared_blocks, _ = parse_bsg(run_dir / f"{machine.bsg_path.stem}_prepared.bsg", catalog_path=None)
        expected_guids = {block.guid for block in prepared_blocks if block.guid}
        if not expected_guids:
            raise BootstrapError("Prepared validation BSG has no block GUIDs.")
        smoke_detail = _validate_smoke_outputs(run_dir=run_dir, expected_guids=expected_guids)
        report.add(
            Stage(
                name="smoke",
                status=STAGE_PASSED,
                message=f"Telemetry smoke passed in {run_dir}",
                detail=smoke_detail,
            )
        )

        # Use the same history-rebuild and Python launch path as the public example.
        from control.run_example import prepare_example, execute_prepared
        demo_dir = datacache_dir() / "control_experiments" / "launcher_demo" / "latest"
        if demo_dir.exists():
            shutil.rmtree(demo_dir)
        demo_dir.mkdir(parents=True, exist_ok=True)
        prepared_example = prepare_example("rocket_orbit_return", demo_dir)
        rebuilt_bsg = prepared_example.bsg
        launcher_controller = prepared_example.controller
        report.add(
            Stage(
                name="launcher_rebuild",
                status=STAGE_PASSED,
                message=str(rebuilt_bsg),
                detail={
                    "record_json": str(repo_root / LAUNCHER_EXAMPLE_JSON),
                    "bsg": str(rebuilt_bsg),
                    "script": str(repo_root / "control" / "examples" / "rocket_orbit_return" / "run.py"),
                },
            )
        )
        print("Starting rocket orbit-and-return example on LONE ORB.", flush=True)
        demo_exit = execute_prepared(
            prepared_example, besiege_data=besiege_data, catalog=catalog_dest,
            launch_timeout=launch_timeout, timeout=90.0, tail_seconds=0,
        )
        if demo_exit != 0:
            raise BootstrapError(f"besiege_cli run launcher demo exited {demo_exit}. See {demo_dir}.")
        _, demo_blocks, _ = parse_bsg(
            demo_dir / f"{rebuilt_bsg.stem}_prepared.bsg",
            catalog_path=None,
        )
        demo_guids = {block.guid for block in demo_blocks if block.guid}
        if not demo_guids:
            raise BootstrapError("Prepared launcher BSG has no block GUIDs.")
        demo_detail = _validate_launcher_demo_outputs(run_dir=demo_dir, expected_guids=demo_guids)
        demo_detail["bsg"] = str(rebuilt_bsg)
        demo_detail["controller"] = str(launcher_controller)
        report.add(
            Stage(
                name="launcher_demo",
                status=STAGE_PASSED,
                message=f"Reusable_Heavy_Launcher LONE ORB orbit demo passed in {demo_dir}",
                detail=demo_detail,
            )
        )

        if session.launched:
            quit_status = quit_game(orchestrator=orchestrator, timeout=30.0)
            report.game_ownership["quit_after"] = quit_status
        else:
            report.game_ownership["quit_after"] = "left_running"
        report.add(
            Stage(
                name="game_cleanup",
                status=STAGE_PASSED,
                message=str(report.game_ownership["quit_after"]),
                detail=report.game_ownership,
            )
        )

        mcp_path = write_mcp_json(repo_root=repo_root)
        report.add(Stage(name="mcp", status=STAGE_PASSED, message=str(mcp_path), detail={"path": str(mcp_path)}))
        report.status = STATUS_PASSED
        persist()
        return report
    except BootstrapBlocked as error:
        report.status = STATUS_BLOCKED
        report.add(Stage(name="blocked", status=STAGE_BLOCKED, message=str(error)))
        persist()
        return report
    except (
        BootstrapError,
        CompatibilityError,
        InspectorError,
        ModdingConfigError,
        FileNotFoundError,
        OrchestratorTimeoutError,
        PreflightError,
        RunFailedError,
        ValueError,
    ) as error:
        report.status = STATUS_FAILED
        report.add(Stage(name="failed", status=STAGE_FAILED, message=str(error)))
        persist()
        return report
    except Exception as error:
        report.status = STATUS_FAILED
        report.add(
            Stage(
                name="failed",
                status=STAGE_FAILED,
                message=f"{type(error).__name__}: {error}",
            )
        )
        persist()
        raise


def report_exit_code(report: BootstrapReport) -> int:
    if report.status == STATUS_PASSED:
        return 0
    if report.status == STATUS_BLOCKED:
        return 2
    return 1
