"""Score one trial: 2.0 telemetry + block map -> 1.0-shaped result."""

from __future__ import annotations

import csv
import random
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import EvalConfig
from . import legacy as L
from .legacy_log import TrackedBlock, clean_like_v1, lua_lines, read_mass_trace, read_ticks, rows_to_csv_text
from .scoring import JUDGES


def mass_ramp(category: str, level: str, cfg: EvalConfig) -> L.MassRamp | None:
    """Cargo mass schedule of a cell, or None when the cell has no cargo."""
    if category == "support":
        ramp = L.SUPPORT_RAMP
    elif category == "transport" and level != "soft":
        ramp = L.TRANSPORT_RAMP
    else:
        return None
    return ramp if cfg.ballast_mass_cap is None else replace(ramp, cap=cfg.ballast_mass_cap)


def v1_block_data(blocks: list[dict]) -> dict[str, str]:
    """1.0 block-id table as load_block_data returns it (sim_common.py:75-84).

    1.0 dumped machine.blocks after to_xml had moved the Starting Block to the
    end (1.0 spatial/build.py:1340-1348, simulation_*.py block_id json), so the
    order is: other blocks by Build ID insertion, then the Starting Block.
    """
    machine = [b for b in blocks if b["legacy_id"] != L.BALLAST_ID]
    if not machine or machine[0]["name_v2"] != "Starting Block":
        raise ValueError("Block table must start with the Starting Block.")
    ordered = machine[1:] + machine[:1]
    return {b["legacy_id"]: L.v1_name(b["name_v2"]) for b in ordered}


def tracked_blocks(blocks: list[dict], category: str, level: str, cfg: EvalConfig) -> list[TrackedBlock]:
    """Blocks whose rows 1.0 printed, in 1.0's print order (machine order, cargo last)."""
    rows = [TrackedBlock(b["legacy_id"], b["guid"], b["name_v1"] or "") for b in blocks]
    if category == "transport":   # simulation_transport.py:87-89
        return [r for r in rows if r.v1_name in ("Starting Block", "Ballast", "Powered Wheel")]
    if category == "support":     # simulation_support.py:79 (only the cargo)
        return [r for r in rows if r.legacy_id == L.BALLAST_ID]
    if category == "lift":        # simulation_lift.py:46-57
        others = [r for r in rows if r.v1_name != "Starting Block" and r.legacy_id != L.BALLAST_ID]
        if level == "soft" or len(others) <= 4 or cfg.lift_tracking == "all":
            return others
        chosen = set(random.Random(cfg.lift_random4_seed).sample(range(len(others)), 4))
        return [r for i, r in enumerate(others) if i in chosen]
    raise ValueError(f"Unknown category {category!r}")


TRACE_NAME = "controller_trace.jsonl"


def find_controller_trace(telemetry_csv: Path) -> Path:
    """The run's controller trace: next to telemetry.csv, or in its run/ directory
    (besiege_cli's run dir, where evalkit's runtime controller writes it)."""
    for candidate in (telemetry_csv.parent / TRACE_NAME, telemetry_csv.parent / "run" / TRACE_NAME):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No {TRACE_NAME} next to {telemetry_csv} or in its run/ directory; "
                            "the cargo mass of a cargo cell comes from it.")


def legacy_rows(telemetry_csv: Path, blocks: list[dict], category: str, level: str,
                cfg: EvalConfig, *, controller_trace: Path | None = None,
                cargo_clock: dict | None = None) -> list[list[str]]:
    """Rows of the 1.0 simulation CSV this run would have produced.

    ``cargo_clock`` (a trial's ``cargo_mass_clock``, evalkit/cargo_plugin.py)
    puts every row on the CargoMass plugin's physics-frame clock, which starts
    at the first frame in simulation as 1.0's Lua clock did: telemetry times
    lose the offset, samples from before that frame are dropped, quantizing and
    the duration cut happen on that clock, and the cargo's mass is the one the
    plugin set for the row's frame. For support (only the cargo is tracked) the
    cargo rows are taken at 1.0's print frames from the plugin's per-frame log."""
    ground = cfg.ground_y if category in ("transport", "lift") else 0.0
    duration = L.DURATION[(category, level)] if cfg.truncate_at_duration else None
    ticks = read_ticks(telemetry_csv)
    tracked = tracked_blocks(blocks, category, level, cfg)
    mass_at = None
    if cargo_clock is not None:
        from .cargo_plugin import cargo_ticks_like_v1, mass_lookup, plugin_frames, to_plugin_clock
        per_frame = plugin_frames(Path(cargo_clock["plugin_log"]))
        ticks = to_plugin_clock(ticks, int(cargo_clock["offset_frames"]))
        mass_at = mass_lookup(per_frame, float(L.BALLAST_DATA_MASS))
        if category == "support":
            cargo = next(b["guid"] for b in blocks if b["legacy_id"] == L.BALLAST_ID)
            ticks = cargo_ticks_like_v1(ticks, per_frame, cargo)
    elif mass_ramp(category, level, cfg) is not None:
        trace = controller_trace or find_controller_trace(Path(telemetry_csv))
        mass_at = read_mass_trace(trace, initial=float(L.BALLAST_DATA_MASS),
                                  delay=cfg.mass_effect_delay).effective_at
    lines = lua_lines(ticks, tracked, mass_at, cfg, ground_y=ground, duration=duration)
    return clean_like_v1(lines, order=cfg.log_line_order)


def v2_extras(telemetry_csv: Path) -> dict[str, Any]:
    """Machine-level 2.0 fields 1.0 did not have."""
    samples = []
    with open(telemetry_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            samples.append((float(row["simulation_time"]), row.get("machine_integrity", ""),
                            row.get("alive_block_count", "")))
    samples.sort(key=lambda s: s[0])
    first = samples[0][1:] if samples else None
    last = samples[-1][1:] if samples else None
    integrity = [float(s[1]) for s in samples if s[1] not in ("", None)]
    lowest = min(integrity) if integrity else None
    return {"machine_integrity_first": first[0] if first else None,
            "machine_integrity_last": last[0] if last else None,
            "machine_integrity_min": lowest,
            "alive_block_count_first": first[1] if first else None,
            "alive_block_count_last": last[1] if last else None}


def score_trial(telemetry_csv: Path, blocks: list[dict], category: str, level: str,
                ident: dict[str, Any], cfg: EvalConfig, *,
                controller_trace: Path | None = None,
                cargo_clock: dict | None = None) -> tuple[dict[str, Any], list[list[str]]]:
    """1.0 result for one trial, plus the 1.0-style rows it was judged on.

    Raises scoring.NoValidRows where 1.0 itself raised (a transport run whose
    tracked blocks printed nothing, a lift run with Water Cannons but no rows);
    everywhere 1.0 fell back to defaults, this returns the same defaults.
    """
    rows = legacy_rows(telemetry_csv, blocks, category, level, cfg, controller_trace=controller_trace,
                       cargo_clock=cargo_clock)
    result = JUDGES[category](rows_to_csv_text(rows), v1_block_data(blocks), {**ident, "category": category,
                                                                               "level": level})
    result["v2"] = v2_extras(telemetry_csv)
    return result, rows
