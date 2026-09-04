"""Strict merger for BuildArenaToolKit telemetry recording sessions.

The in-game TelemetryRecorder writes each session as chunked CSV files plus
one final manifest (schema buildarena.telemetry_recording.v2):

    [basename]__[utc]__traj_NNNNN.csv   sample_sequence, simulation_time,
                                        block_guid, then columns for the
                                        negotiated target/machine fields
    [basename]__[utc]__keys_NNNNN.csv   event_sequence, simulation_time,
                                        key, event
    [basename]__[utc]__manifest.json    chunk lists, counts, timing config,
                                        completed flag, abort error

This module reassembles a session out-of-game and refuses to produce output
from anything inconsistent: missing/renamed chunks, header drift, sequence
gaps or regressions, non-monotonic time, row-count mismatches against the
manifest, or a destroyed target reappearing after it disappeared all raise
TelemetrySessionError. A session whose manifest is missing,
or that carries completed=false / an error, is explicitly incomplete: it is
only merged when the caller passes allow_incomplete=True, and even then the
per-chunk consistency checks still apply to everything that was flushed.
There is no interpolation and no gap-filling anywhere in this module.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

TELEMETRY_RECORDING_SCHEMA = "buildarena.telemetry_recording.v2"
TELEMETRY_RECORDING_SCHEMAS = (
    "buildarena.telemetry_recording.v2",
)

_TRAJECTORY_PREFIX = ["sample_sequence", "simulation_time", "block_guid"]
_TRAJECTORY_HEADER = ["sample_sequence", "simulation_time", "block_guid", "x", "y", "z"]
_KEY_HEADER = ["event_sequence", "simulation_time", "key", "event"]

# Sample times are exact multiples of fixed_delta_time computed in float32
# by the mod; float64 re-reading warrants a tight but nonzero tolerance.
_TIME_TOLERANCE = 1e-4


class TelemetrySessionError(RuntimeError):
    """A recording session is inconsistent with its manifest or with the
    recorder's protocol. Raised instead of returning partial output."""


@dataclass(frozen=True)
class TrajectoryRow:
    sample_sequence: int
    simulation_time: float
    block_guid: str
    x: float | None
    y: float | None
    z: float | None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class KeyEventRow:
    event_sequence: int
    simulation_time: float
    key: str
    event: str


@dataclass(frozen=True)
class TelemetrySession:
    manifest_path: Path
    manifest: dict
    trajectory: list[TrajectoryRow] = field(repr=False)
    key_events: list[KeyEventRow] = field(repr=False)
    trajectory_header: list[str] = field(default_factory=lambda: list(_TRAJECTORY_HEADER))

    @property
    def completed(self) -> bool:
        return bool(self.manifest.get("completed"))

    @property
    def error(self) -> str:
        return str(self.manifest.get("error", "") or "")

    @property
    def target_guids(self) -> tuple[str, ...]:
        return tuple(str(guid).lower() for guid in self.manifest["target_guids"])


def list_session_manifests(data_dir: str | Path, basename: str | None = None) -> list[Path]:
    """All session manifests in a ToolKit data dir, oldest first. Chunk sets
    without a manifest (game died before finalize) are not listed here; use
    find_orphan_chunks to surface them explicitly."""
    pattern = f"{basename}__*__manifest.json" if basename else "*__manifest.json"
    return sorted(Path(data_dir).glob(pattern), key=lambda path: path.stat().st_mtime)


def find_orphan_chunks(data_dir: str | Path) -> list[Path]:
    """Trajectory/key chunk files whose session has no manifest: the game
    stopped before the session was finalized. These are surfaced, never
    silently merged."""
    root = Path(data_dir)
    manifest_sessions = {
        path.name.rsplit("__manifest.json", 1)[0] for path in root.glob("*__manifest.json")
    }
    orphans: list[Path] = []
    for chunk in sorted(root.glob("*__traj_*.csv")) + sorted(root.glob("*__keys_*.csv")):
        session = chunk.name.rsplit("__", 1)[0]
        if session not in manifest_sessions:
            orphans.append(chunk)
    return orphans


def load_manifest(manifest_path: str | Path) -> dict:
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    schema = manifest.get("schema")
    if schema != TELEMETRY_RECORDING_SCHEMA:
        raise TelemetrySessionError(
            f"Unsupported recording manifest schema {schema!r} in {path} "
            f"(expected {TELEMETRY_RECORDING_SCHEMA!r})."
        )
    for key in (
        "session",
        "sample_rate_hz",
        "fixed_delta_time",
        "sample_interval_frames",
        "target_guids",
        "sample_count",
        "trajectory_rows",
        "key_event_count",
        "trajectory_chunks",
        "key_chunks",
        "completed",
        "error",
        "run_id",
        "machine_sha256",
        "source_sha256",
        "managed",
    ):
        if key not in manifest:
            raise TelemetrySessionError(f"Recording manifest {path} is missing key {key!r}.")
    if not manifest["target_guids"]:
        raise TelemetrySessionError(f"Recording manifest {path} lists no target guids.")
    return manifest


def _read_chunk(path: Path, expected_header: list[str]) -> list[list[str]]:
    if not path.is_file():
        raise TelemetrySessionError(f"Chunk file listed in the manifest is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise TelemetrySessionError(f"Chunk file is empty (not even a header): {path}") from None
        if header != expected_header:
            raise TelemetrySessionError(
                f"Chunk header mismatch in {path}: expected {expected_header}, got {header}."
            )
        rows = [row for row in reader if row]
    if not rows:
        raise TelemetrySessionError(f"Chunk file has a header but no data rows: {path}")
    for row in rows:
        if len(row) != len(expected_header):
            raise TelemetrySessionError(f"Malformed row in {path}: {row!r}")
    return rows


def _header_from_manifest_or_chunk(manifest: dict, data_dir: Path) -> list[str]:
    declared = manifest.get("trajectory_columns")
    if isinstance(declared, str) and declared.strip():
        return [part.strip() for part in declared.split(",") if part.strip()]
    if isinstance(declared, list) and declared:
        return [str(part) for part in declared]
    first_chunk = manifest.get("trajectory_chunks") or []
    if not first_chunk:
        return list(_TRAJECTORY_HEADER)
    path = data_dir / str(first_chunk[0])
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
    if not header or header[:3] != _TRAJECTORY_PREFIX:
        raise TelemetrySessionError(
            f"Trajectory chunk {path} is missing the required prefix {_TRAJECTORY_PREFIX}."
        )
    return header


def _merge_trajectory(manifest: dict, data_dir: Path) -> tuple[list[TrajectoryRow], list[str]]:
    header = _header_from_manifest_or_chunk(manifest, data_dir)
    extra_names = header[3:]
    rows: list[TrajectoryRow] = []
    for chunk_name in manifest["trajectory_chunks"]:
        for raw in _read_chunk(data_dir / str(chunk_name), header):
            extras = {name: raw[3 + index] for index, name in enumerate(extra_names)}
            rows.append(
                TrajectoryRow(
                    sample_sequence=int(raw[0]),
                    simulation_time=float(raw[1]),
                    block_guid=raw[2].lower(),
                    x=float(extras["x"]) if extras.get("x") not in (None, "") else None,
                    y=float(extras["y"]) if extras.get("y") not in (None, "") else None,
                    z=float(extras["z"]) if extras.get("z") not in (None, "") else None,
                    extra=extras,
                )
            )

    if len(rows) != int(manifest["trajectory_rows"]):
        raise TelemetrySessionError(
            f"Trajectory row count mismatch: manifest says {manifest['trajectory_rows']}, "
            f"chunks contain {len(rows)}."
        )
    if not rows:
        return rows

    targets = {str(guid).lower() for guid in manifest["target_guids"]}
    expected_delta = float(manifest["fixed_delta_time"]) * int(manifest["sample_interval_frames"])

    # Group rows by sample_sequence in file order. Destroyed targets may
    # disappear permanently; surviving targets remain unique and share one
    # strictly advancing simulation_time per sample.
    sample_count = int(manifest["sample_count"])
    index = 0
    previous_time: float | None = None
    surviving_targets = set(targets)
    for expected_sequence in range(sample_count):
        start = index
        while index < len(rows) and rows[index].sample_sequence == expected_sequence:
            index += 1
        group = rows[start:index]
        if not group:
            raise TelemetrySessionError(
                f"Sample {expected_sequence} has no surviving target rows."
            )
        guids = [row.block_guid for row in group]
        guid_set = set(guids)
        if len(guids) != len(guid_set):
            raise TelemetrySessionError(
                f"Sample {expected_sequence} repeats a target GUID: {guids}."
            )
        unexpected = guid_set - surviving_targets
        if unexpected:
            raise TelemetrySessionError(
                f"Sample {expected_sequence} contains targets that were absent from an "
                f"earlier sample or the manifest: {sorted(unexpected)}."
            )
        surviving_targets = guid_set
        times = {row.simulation_time for row in group}
        if len(times) != 1:
            raise TelemetrySessionError(
                f"Sample {expected_sequence} carries multiple simulation_time values: {sorted(times)}."
            )
        time_value = group[0].simulation_time
        if previous_time is not None:
            delta = time_value - previous_time
            if delta <= 0.0:
                raise TelemetrySessionError(
                    f"simulation_time is not strictly increasing at sample {expected_sequence}: "
                    f"{previous_time} -> {time_value}."
                )
            if not math.isclose(delta, expected_delta, rel_tol=0.0, abs_tol=_TIME_TOLERANCE):
                raise TelemetrySessionError(
                    f"Sample period violation at sample {expected_sequence}: delta {delta!r} != "
                    f"expected {expected_delta!r} (fixed_delta_time * sample_interval_frames). "
                    "The recording was not sampled on strict integer physics-frame boundaries."
                )
        previous_time = time_value
    if index != len(rows):
        raise TelemetrySessionError(
            f"Trajectory contains {len(rows) - index} rows beyond the manifest's "
            f"sample_count={sample_count}."
        )
    return rows, header


def _merge_key_events(manifest: dict, data_dir: Path) -> list[KeyEventRow]:
    rows: list[KeyEventRow] = []
    for chunk_name in manifest["key_chunks"]:
        for raw in _read_chunk(data_dir / str(chunk_name), _KEY_HEADER):
            rows.append(
                KeyEventRow(
                    event_sequence=int(raw[0]),
                    simulation_time=float(raw[1]),
                    key=raw[2],
                    event=raw[3],
                )
            )
    if len(rows) != int(manifest["key_event_count"]):
        raise TelemetrySessionError(
            f"Key event count mismatch: manifest says {manifest['key_event_count']}, "
            f"chunks contain {len(rows)}."
        )
    for position, row in enumerate(rows):
        if row.event_sequence != position:
            raise TelemetrySessionError(
                f"event_sequence discontinuity at position {position}: got {row.event_sequence}."
            )
        if row.event not in {"down", "up"}:
            raise TelemetrySessionError(f"Unknown key event kind {row.event!r} at position {position}.")
        if position > 0 and row.simulation_time < rows[position - 1].simulation_time:
            raise TelemetrySessionError(
                f"Key event time went backwards at position {position}: "
                f"{rows[position - 1].simulation_time} -> {row.simulation_time}."
            )
    return rows


def load_session(manifest_path: str | Path, *, allow_incomplete: bool = False) -> TelemetrySession:
    """Merge one recording session's chunks into memory with full
    consistency checking.

    allow_incomplete=True is the explicit opt-in for salvaging a session the
    mod marked completed=false (destroyed target, superseded episode, ...).
    The recorder finalizes counts and chunk lists even for aborted sessions,
    so every consistency check still applies in full; only the
    completed-flag gate is bypassed. Sessions with no manifest at all (game
    death) are never merged - see find_orphan_chunks.
    """
    path = Path(manifest_path)
    manifest = load_manifest(path)
    completed = bool(manifest.get("completed")) and not str(manifest.get("error", "") or "")
    if not completed and not allow_incomplete:
        raise TelemetrySessionError(
            f"Session {manifest.get('session')!r} is incomplete "
            f"(completed={manifest.get('completed')}, error={manifest.get('error')!r}). "
            "Pass allow_incomplete=True to merge what was flushed anyway."
        )
    trajectory, header = _merge_trajectory(manifest, path.parent)
    key_events = _merge_key_events(manifest, path.parent)
    return TelemetrySession(
        manifest_path=path,
        manifest=manifest,
        trajectory=trajectory,
        key_events=key_events,
        trajectory_header=header,
    )


def write_merged_csvs(
    session: TelemetrySession,
    *,
    trajectory_csv: str | Path,
    key_events_csv: str | Path | None = None,
) -> None:
    """Write the merged session back out as single flat CSV files with the
    same raw columns the recorder produced - no derived fields are added."""
    trajectory_path = Path(trajectory_csv)
    trajectory_path.parent.mkdir(parents=True, exist_ok=True)
    with trajectory_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(session.trajectory_header)
        extra_names = session.trajectory_header[3:]
        for row in session.trajectory:
            values = [row.sample_sequence, repr(row.simulation_time), row.block_guid]
            for name in extra_names:
                raw = row.extra.get(name, "")
                if name in {"x", "y", "z"} and raw != "":
                    values.append(repr(float(raw)))
                else:
                    values.append(raw)
            writer.writerow(values)
    if key_events_csv is not None:
        key_path = Path(key_events_csv)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        with key_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(_KEY_HEADER)
            for event in session.key_events:
                writer.writerow(
                    [event.event_sequence, repr(event.simulation_time), event.key, event.event]
                )
