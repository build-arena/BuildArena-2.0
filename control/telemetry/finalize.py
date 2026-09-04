"""Bind a recorder session to one run and write its telemetry outputs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from controller_sdk.protocol import RUN_SUMMARY_SCHEMA, atomic_write_json

from .recording import (
    TELEMETRY_RECORDING_SCHEMA,
    TelemetrySessionError,
    list_session_manifests,
    load_manifest,
    load_session,
    write_merged_csvs,
)


class RecorderBindingError(TelemetrySessionError):
    """A managed recorder session could not be bound exactly to this run."""


def find_bound_manifest(
    data_dir: Path,
    *,
    run_id: str,
    machine_sha256: str,
    source_sha256: str,
) -> Path:
    """Return the single session manifest that matches run_id and hashes.

    Never selects "the latest session" in the data directory.
    """
    if not run_id:
        raise RecorderBindingError("Managed recorder finalize requires a non-empty run_id.")
    if not machine_sha256 or not source_sha256:
        raise RecorderBindingError(
            "Managed recorder finalize requires machine_sha256 and source_sha256."
        )
    matches: list[Path] = []
    for path in list_session_manifests(data_dir):
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if raw.get("schema") != TELEMETRY_RECORDING_SCHEMA:
            continue
        if (
            str(raw.get("run_id", "")) != run_id
            or str(raw.get("machine_sha256", "")) != machine_sha256
            or str(raw.get("source_sha256", "")) != source_sha256
        ):
            continue
        load_manifest(path)
        matches.append(path)
    if not matches:
        raise RecorderBindingError(
            f"No TelemetryRecorder manifest matches run_id={run_id!r} "
            f"machine_sha256={machine_sha256[:12]} source_sha256={source_sha256[:12]}."
        )
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise RecorderBindingError(
            f"Multiple TelemetryRecorder manifests match run_id={run_id!r}: {names}."
        )
    return matches[0]


def wait_for_bound_manifest(
    data_dir: Path,
    *,
    run_id: str,
    machine_sha256: str,
    source_sha256: str,
    timeout: float = 30.0,
    poll_interval: float = 0.2,
) -> Path:
    """Wait until the bound manifest exists, completed=true, and error is empty."""
    deadline = time.monotonic() + timeout
    last_error = "no matching recorder manifest yet"
    while time.monotonic() < deadline:
        try:
            path = find_bound_manifest(
                data_dir,
                run_id=run_id,
                machine_sha256=machine_sha256,
                source_sha256=source_sha256,
            )
            manifest = load_manifest(path)
            completed = bool(manifest.get("completed")) and not str(
                manifest.get("error", "") or ""
            )
            if completed:
                return path
            last_error = (
                f"{path.name} is not complete "
                f"(completed={manifest.get('completed')}, error={manifest.get('error')!r})"
            )
        except RecorderBindingError as exc:
            last_error = str(exc)
        time.sleep(poll_interval)
    raise RecorderBindingError(
        f"Timed out after {timeout}s waiting for recorder completion: {last_error}"
    )


def finalize_run(
    *,
    run_dir: Path,
    data_dir: Path,
    selection: dict[str, Any],
    extra: dict[str, Any] | None = None,
    run_id: str = "",
    machine_sha256: str = "",
    source_sha256: str = "",
    recorder_enabled: bool = True,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    omitted: list[str] = []
    telemetry_csv = run_dir / "telemetry.csv"
    key_csv = run_dir / "key_events.csv"
    if recorder_enabled:
        if not run_id:
            raise RecorderBindingError(
                "finalize_run requires run_id when the TelemetryRecorder is enabled."
            )
        manifest_path = find_bound_manifest(
            data_dir,
            run_id=run_id,
            machine_sha256=machine_sha256,
            source_sha256=source_sha256,
        )
        session = load_session(manifest_path, allow_incomplete=allow_incomplete)
        write_merged_csvs(session, trajectory_csv=telemetry_csv, key_events_csv=key_csv)
        (run_dir / "telemetry_manifest.json").write_text(
            json.dumps(session.manifest, indent=2), encoding="utf-8"
        )
    else:
        omitted.append("TelemetryRecorder disabled for this run")

    summary: dict[str, Any] = {
        "schema": RUN_SUMMARY_SCHEMA,
        "run_id": run_id,
        "profile": selection.get("profile"),
        "target_fields": list(selection.get("target_fields") or ()),
        "machine_fields": list(selection.get("machine_fields") or ()),
        "omitted_outputs": omitted,
    }
    if extra:
        summary.update(extra)
    atomic_write_json(run_dir / "summary.json", summary)
    return summary
