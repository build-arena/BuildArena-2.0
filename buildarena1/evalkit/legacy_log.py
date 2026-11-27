"""Turn 2.0 telemetry into the rows a 1.0 simulation CSV would hold.

1.0 never measured anything in Python: an embedded Lua script printed
``time, ID_<n>, x, y, z`` (plus a burning flag for tracked Water Cannons and the
commanded mass for the Ballast) every 4 physics frames, and
simulation/operations.py copied the log window into a CSV, deduplicated and
sorted it. The 1.0 judges read that CSV. This module reproduces the printing
(spatial/build.py:1300-1316) and the cleaning (operations.py:128-134, 322-378)
from a 2.0 ``telemetry.csv`` so the ported judges see the same rows.
"""

from __future__ import annotations

import bisect
import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .config import EvalConfig
from .legacy import BALLAST_ID


@dataclass(frozen=True)
class TrackedBlock:
    legacy_id: str      # Build ID as a string, or "Ballast"
    guid: str
    v1_name: str


@dataclass(frozen=True)
class Tick:
    time: float
    rows: dict[str, dict[str, str]]   # guid (lower case) -> telemetry row


def read_ticks(telemetry_csv: Path) -> list[Tick]:
    """Group telemetry rows by (sample_sequence, simulation_time), ordered by time."""
    ticks: list[Tick] = []
    index: dict[tuple[str, str], Tick] = {}
    with open(telemetry_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row["sample_sequence"], row["simulation_time"])
            tick = index.get(key)
            if tick is None:
                tick = Tick(time=float(row["simulation_time"]), rows={})
                index[key] = tick
                ticks.append(tick)
            tick.rows[row["block_guid"].lower()] = row
    ticks.sort(key=lambda tk: tk.time)   # rows' order in the file does not matter
    return ticks


@dataclass(frozen=True)
class MassTrace:
    """Ballast masses the run's controller sent, from controller_trace.jsonl.

    1.0's Lua set the slider and printed the same variable every frame
    (spatial/build.py:1289-1292, 1312-1313), so the logged mass is the mass in
    effect. In 2.0 the controller sends a value at a telemetry sample time;
    the game applies it after that sample. ``effective_at(t)`` is the last value
    sent strictly before ``t - delay``; before the first send it is the mass
    written into the BSG.
    """
    times: tuple[float, ...]
    masses: tuple[float, ...]
    initial: float
    delay: float = 0.0

    def effective_at(self, t: float) -> float:
        i = bisect.bisect_left(self.times, t - self.delay) - 1
        return self.masses[i] if i >= 0 else self.initial


def read_mass_trace(path: Path, *, initial: float, delay: float = 0.0) -> MassTrace:
    """Read the ``{"t": ..., "mass": ...}`` lines a controller trace holds (also
    the aligned CargoMass trace, evalkit/cargo_plugin.py)."""
    sent = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                if "mass" in record:
                    sent.append((float(record["t"]), float(record["mass"])))
    sent.sort(key=lambda item: item[0])
    return MassTrace(tuple(t for t, _ in sent), tuple(m for _, m in sent), initial, delay)


def _is_heated(row: dict[str, str], field: str) -> bool:
    valid = row.get("fire_valid" if field == "fire_burning" else "steam_valid", "")
    value = row.get(field, "")
    return valid.strip() == "1" and value.strip() not in ("", "0") and float(value) > 0.5


def lua_lines(ticks: list[Tick], tracked: list[TrackedBlock], mass_at: Callable[[float], float] | None,
              cfg: EvalConfig, *, ground_y: float, duration: float | None) -> list[str]:
    """Lines the 1.0 Lua script would have printed, spatial/build.py:1300-1316.
    ``mass_at`` gives the cargo mass in effect at a sample time."""
    fmt = (lambda v: "%.1f" % v) if cfg.quantize_like_lua else (lambda v: repr(float(v)))
    lines: list[str] = []
    for tick in ticks:
        if duration is not None and tick.time > duration + 1e-6:
            continue
        t = tick.time
        for block in tracked:
            row = tick.rows.get(block.guid.lower())
            if row is None:
                continue
            tag = f"ID_{block.legacy_id}"
            if block.v1_name == "Water Cannon":
                heated = "true" if _is_heated(row, cfg.heated_field) else "false"
                lines.append(f"{fmt(t)}, {tag}, {heated}")
            if block.legacy_id == BALLAST_ID:
                if mass_at is None:
                    raise ValueError("A tracked Ballast needs its mass record.")
                lines.append(f"{fmt(t)}, {tag}, {fmt(mass_at(t))}")
            x, y, z = float(row["x"]), float(row["y"]) - ground_y, float(row["z"])
            lines.append(f"{fmt(t)}, {tag}, {fmt(x)}, {fmt(y)}, {fmt(z)}")
    return lines


def clean_like_v1(lines: list[str], *, order: str = "print") -> list[list[str]]:
    """save_to_csv (operations.py:128-134) then clean_simulation_csv (322-378)."""
    if order == "reversed":
        lines = list(reversed(lines))
    elif order != "print":
        raise ValueError(f"Unknown log line order {order!r}.")
    seen: set[tuple[str, ...]] = set()
    rows: list[list[str]] = []
    for line in lines:
        row = [item.strip() for item in line.split(",")]
        if len(row) < 2 or row[-1].strip().endswith(("-", ".", ",")):
            continue
        key = tuple(row)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda r: float(r[0]))
    return rows


def rows_to_csv_text(rows: list[list[str]]) -> str:
    """The cleaned CSV as operations.py writes it (csv.writer defaults)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def trajectory_feedback(rows: list[list[str]], *, axis_labels: str = "1.0") -> tuple[str, bool]:
    """Whole-second trajectory text for the control agent and the not-moving flag.

    scheduler/runner.py:202-228. Positions are relative to the first kept row.
    With axis_labels "1.0", y carries the vertical and z the northward offset,
    as runner.py:225-226 prints them.
    """
    valid = []
    for row in rows:
        if len(row) == 5 and float(row[0]) % 1 < 0.001 and not row[-1].strip().endswith(("-", ".", ",")):
            valid.append([row[0], row[1], float(row[2]), float(row[4]), float(row[3])])
    if not valid:
        raise ValueError("No whole-second position rows to build trajectory feedback from.")
    positions = np.array([r[2:5] for r in valid], dtype=float)
    not_moving = bool(np.all(np.abs(positions - np.mean(positions, axis=0)) < 1))
    init = valid[0]
    out = ["The simulation trajectory (time, Block ID, x, y, z): "]
    for row in valid:
        d = [row[i] - init[i] for i in (2, 3, 4)]          # east, north, up
        vertical_as_y = axis_labels == "1.0"
        y, z = (d[2], d[1]) if vertical_as_y else (d[1], d[2])
        out.append(f"t={float(row[0]):.1f}, ID={row[1]}, x={d[0]:.2f}, y={y:.2f}, z={z:.2f}")
    return "\n".join(out).replace("ID_Ballast", "Cargo Load"), not_moving
