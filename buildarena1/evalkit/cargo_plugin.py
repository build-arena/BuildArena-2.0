"""The cargo-mass plugin's log, put on the telemetry clock.

The plugin (README section 6; not part of this repository) sets the cargo's
Rigidbody mass on 1.0's curve and logs one line per physics frame: ``frame`` (counted from the first frame in
simulation, as 1.0's Lua counted ``frame_count``), ``mass`` (the curve value,
the mass the cargo held for that frame's physics step) and ``y`` (the cargo's
height read before that step).

The ToolKit's telemetry clock (``simulation_time``) starts a few frames
earlier, and how many varies from run to run (8 to 10 frames in the runs we
measured). The offset is therefore read off each
run's data: telemetry samples the cargo's position at the same point of a
physics frame as the plugin reads ``y``, so at the right offset the two heights
agree to the digits telemetry prints while the cargo falls onto the machine.

Per-frame lines are the ones without an ``event`` field or with
``"event": "frame"``; ``start``, ``stop`` and the other events are not frames.
After the offset is found, the log must reach the frame of the last telemetry
sample at that offset (``align`` raises otherwise), so every judged row has
the mass the plugin set for it.

The aligned trace has the controller-trace format that
``legacy_log.read_mass_trace`` reads: one ``{"t", "mass"}`` line per plugin
frame f, with t = (f + offset) / 100 - 0.005. ``MassTrace.effective_at(T)``
takes the last line strictly before T, so a telemetry sample at T is paired
with the mass set for the very frame that sample shows, which is how 1.0's Lua
paired a printed position with the mass it had just set (spatial/build.py:1286-1316).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

MAX_OFFSET = 40            # frames
MATCH_TOL = 1e-3           # height units; telemetry prints about 8 significant digits
ALIGN_SECONDS = 2.0        # telemetry samples used, from the start


def read_log(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def frames(records: list[dict]) -> list[dict]:
    """The per-frame records: no ``event`` field, or ``"event": "frame"``."""
    return [r for r in records if r.get("event", "frame") == "frame" and "frame" in r and "mass" in r]


def last_telemetry_frame(telemetry_csv: Path) -> int:
    """round(simulation_time * 100) of the last telemetry sample, any block."""
    last = None
    with open(telemetry_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            t = float(row["simulation_time"])
            last = t if last is None or t > last else last
    if last is None:
        raise ValueError("Telemetry has no rows.")
    return round(last * 100)


def cargo_heights(telemetry_csv: Path, cargo_guid: str) -> dict[int, float]:
    """Telemetry frame index (round(simulation_time * 100)) -> cargo y."""
    out = {}
    with open(telemetry_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["block_guid"].lower() == cargo_guid.lower() and row.get("y") not in (None, ""):
                out[round(float(row["simulation_time"]) * 100)] = float(row["y"])
    return out


def find_offset(records: list[dict], heights: dict[int, float]) -> dict:
    """The frame offset at which the plugin's heights match telemetry's."""
    plugin_y = {int(r["frame"]): float(r["y"]) for r in frames(records) if "y" in r}
    if not plugin_y:
        raise ValueError("The CargoMass log has no per-frame heights to align with telemetry.")
    scored = []
    for off in range(0, MAX_OFFSET + 1):
        errors = [abs(plugin_y[k - off] - y) for k, y in heights.items()
                  if k <= ALIGN_SECONDS * 100 and (k - off) in plugin_y]
        if len(errors) >= 5:
            scored.append((max(errors), off, len(errors)))
    if not scored:
        raise ValueError("Too few telemetry samples overlap the CargoMass log to align the two clocks.")
    scored.sort()
    best_err, best_off, n = scored[0]
    ties = sorted(off for err, off, _ in scored if err <= best_err + 1e-6)
    second = next((err for err, off, _ in scored if off not in ties), None)
    if best_err > MATCH_TOL:
        raise ValueError(f"No frame offset makes the CargoMass heights match telemetry (best {best_off}: "
                         f"max difference {best_err:.4g} over {n} samples).")
    # ties: the cargo did not move in the window (fake runs); the smallest offset is taken and flagged
    return {"offset_frames": ties[0], "max_height_difference": best_err, "samples": n,
            "next_best_difference": second, "ambiguous": len(ties) > 1}


def align(log_path: Path, telemetry_csv: Path, cargo_guid: str, out_path: Path) -> dict:
    """Write the aligned trace to ``out_path``; return what was found."""
    records = read_log(log_path)
    found = find_offset(records, cargo_heights(telemetry_csv, cargo_guid))
    off = found["offset_frames"]
    held = sorted((int(r["frame"]), float(r["mass"])) for r in frames(records))
    need = last_telemetry_frame(telemetry_csv) - off
    if not held or held[-1][0] < need:
        raise ValueError(f"The CargoMass log ends at frame {held[-1][0] if held else None}; at the measured offset of "
                         f"{off} frames the last telemetry sample is frame {need}, so the end of the run has no mass.")
    with open(out_path, "w", encoding="utf-8") as handle:
        for f, m in held:
            handle.write(json.dumps({"t": (f + off) / 100 - 0.005, "mass": m, "plugin_frame": f}) + "\n")
    start = next((r for r in records if r.get("event") == "start"), {})
    stop = next((r for r in records if r.get("event") == "stop"), {})
    return {**found, "aligned_trace": str(out_path), "plugin_log": str(log_path),
            "frames": len(held), "switch_frames": start.get("switch_frames"),
            "guid_member": start.get("guid_member"), "k": start.get("k"),
            "post_step_mismatch": stop.get("post_step_mismatch"), "late_fixes": stop.get("late_fixes"),
            "writes": stop.get("writes")}


# ----------------------------------------------------------------- judging clock
PRINT_EVERY = 4            # 1.0's Lua printed when frame_count % (100 / refresh_rate) == 0, refresh_rate 25


def plugin_frames(log_path: Path) -> dict[int, dict]:
    """Plugin frame -> {"mass", "y"} from the per-frame log."""
    return {int(r["frame"]): {"mass": float(r["mass"]), "y": float(r["y"]) if "y" in r else None}
            for r in frames(read_log(log_path))}


def to_plugin_clock(ticks: list, offset: int) -> list:
    """Telemetry ticks on the plugin's frame clock: time (frame) = telemetry frame - offset.
    Samples from before the plugin's first physics frame (the switch into
    simulation) are dropped; 1.0's Lua clock started at that frame too."""
    from .legacy_log import Tick
    out = []
    for tick in ticks:
        frame = round(tick.time * 100) - offset
        if frame >= 1:
            out.append(Tick(time=frame / 100, rows=tick.rows))
    return out


def mass_lookup(per_frame: dict[int, dict], initial: float):
    """mass_at(t) on the plugin clock: the mass set for frame round(t * 100) (the
    last logged frame at or before it), as 1.0 printed the mass it had just set."""
    import bisect
    keys = sorted(per_frame)

    def mass_at(t: float) -> float:
        i = bisect.bisect_right(keys, round(t * 100)) - 1
        return per_frame[keys[i]]["mass"] if i >= 0 else initial
    return mass_at


def cargo_ticks_like_v1(clock_ticks: list, per_frame: dict[int, dict], cargo_guid: str) -> list:
    """The cargo's rows at 1.0's print frames (frame % 4 == 0) from the plugin's log.

    The height is the plugin's own reading for that frame (the same point of the
    frame telemetry samples). x and z are interpolated from the telemetry
    samples around that frame; the support judge does not read them
    (scoring.ballast_stats uses time, height and mass)."""
    from .legacy_log import Tick
    guid = cargo_guid.lower()
    known = sorted((round(t.time * 100), t.rows[guid]) for t in clock_ticks if guid in t.rows)
    if not known:
        return []
    out = []
    last = max(per_frame)
    for frame in range(PRINT_EVERY, last + 1, PRINT_EVERY):
        rec = per_frame.get(frame)
        if rec is None or rec["y"] is None:
            continue
        before = [k for k in known if k[0] <= frame]
        after = [k for k in known if k[0] >= frame]
        a = before[-1] if before else after[0]
        b = after[0] if after else before[-1]
        w = 0.0 if b[0] == a[0] else (frame - a[0]) / (b[0] - a[0])
        row = dict(a[1])
        for axis in ("x", "z"):
            row[axis] = repr(float(a[1][axis]) + w * (float(b[1][axis]) - float(a[1][axis])))
        row["y"] = repr(rec["y"])
        out.append(Tick(time=frame / 100, rows={guid: row}))
    return out
