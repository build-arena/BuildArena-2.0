"""Synthetic trajectories in 2.0 telemetry form, for the judge tests.

A scenario is a machine (blocks in Build ID order, optional cargo) and a
function giving each block's game-coordinate position and heated flag at a
time. ``write`` produces a telemetry.csv with the 2.0 column set (header copied
from a real 2.0 run) and returns what both judges need.
"""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

HEADER = ("sample_sequence,simulation_time,block_guid,x,y,z,qx,qy,qz,qw,vx,vy,vz,avx,avy,avz,"
          "fuel,fuel_capacity,fuel_valid,fire_burning,fire_burned,fire_intensity,fire_valid,steam,"
          "steam_valid,ice,ice_valid,health,health_max,health_valid,buoyancy,buoyancy_min,buoyancy_max,"
          "buoyancy_valid,machine_integrity,alive_block_count").split(",")

V2_NAMES = {"Starting Block": "Starting Block", "Small Wooden Block": "Single Wooden Block",
            "Powered Wheel": "Wheel", "Water Cannon": "Water Cannon", "Torch": "Torch",
            "Brace": "Brace", "Winch": "Rope Winch", "Ballast": "Ballast"}

State = Callable[[str, float], tuple[float, float, float, bool] | None]


@dataclass
class Scenario:
    category: str
    level: str
    v1_names: list[str]                 # Build IDs 1..n in order; first is Starting Block
    state: State                        # (legacy_id, t) -> (x, y, z, heated) or None
    cargo: bool = False
    duration: float = 30.0
    times: list[float] = field(default_factory=list)

    def blocks(self) -> list[dict]:
        rows = []
        for i, name in enumerate(self.v1_names, start=1):
            rows.append({"build_id": str(i), "legacy_id": str(i), "name_v1": name, "name_v2": V2_NAMES[name],
                         "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"test:{i}")), "block_type_id": "0"})
        if self.cargo:
            rows.append({"build_id": None, "legacy_id": "Ballast", "name_v1": "Ballast", "name_v2": "Ballast",
                         "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, "test:Ballast")), "block_type_id": "35"})
        return rows


def telemetry_times(duration: float, hz: int = 25) -> list[float]:
    """Sample times as 2.0 writes them: a float32 running sum of the step."""
    t, step, out = np.float32(0.0), np.float32(1.0 / hz), []
    while float(t) < duration + 0.5:
        t = np.float32(t + step)
        out.append(float(t))
    return out


def write(scenario: Scenario, path: Path) -> list[tuple[float, dict]]:
    """Write telemetry.csv; return ticks as (time, {legacy_id: (x, y, z, heated)})."""
    times = scenario.times or telemetry_times(scenario.duration)
    blocks = scenario.blocks()
    ticks = []
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for seq, t in enumerate(times):
            state = {}
            for block in blocks:
                value = scenario.state(block["legacy_id"], t)
                if value is None:
                    continue
                state[block["legacy_id"]] = value
                x, y, z, heated = value
                row = dict.fromkeys(HEADER, "")
                row.update(sample_sequence=seq, simulation_time=repr(t), block_guid=block["guid"],
                           x=repr(float(x)), y=repr(float(y)), z=repr(float(z)), qx=0, qy=0, qz=0, qw=1,
                           fire_burning=int(bool(heated)), fire_valid=1, steam=0, steam_valid=1,
                           machine_integrity=1.0, alive_block_count=len(blocks))
                writer.writerow([row[h] for h in HEADER])
            ticks.append((t, state))
    return ticks


def v1_tracked(scenario: Scenario, blocks: list[dict]) -> list[tuple[str, str]]:
    """1.0 tracking set in print order, written from simulation_*.py directly."""
    named = [(b["legacy_id"], b["name_v1"]) for b in blocks]
    if scenario.category == "transport":
        return [(i, n) for i, n in named if n in ("Starting Block", "Ballast", "Powered Wheel")]
    if scenario.category == "support":
        return [(i, n) for i, n in named if n == "Ballast"]
    return [(i, n) for i, n in named if n not in ("Starting Block", "Ballast")]


def v1_block_id_list(blocks: list[dict]) -> list[dict]:
    """block_id json as 1.0 dumps it after to_xml moved the Starting Block last."""
    entries = [{b["legacy_id"]: b["name_v1"]} for b in blocks]
    return entries[1:] + entries[:1]
