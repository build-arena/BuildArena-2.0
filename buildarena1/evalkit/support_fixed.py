"""Support cells with one fixed cargo mass per run: the standby protocol for a
game without the cargo-mass plugin (README section 6). Not used for 1.0-style
scores; result.json flags it.

The game clips any cargo mass sent at run time to its slider range (3 in build
24131898), so without the plugin 1.0's ramp from 5 to 1500 cannot be played.
Instead every run writes one mass M into the BSG, puts the cargo 0.1 above the
bridge (``cargo_drop_above_top``), sends no mass and runs
``support_run_seconds`` (10) simulation seconds on the stand-in banks of
evalkit/terrain.py.

* A run holds M when the cargo height (the Ballast's transform y, as 1.0's
  Lua printed it, rounded to 0.1 when ``quantize_like_lua``) never drops below 4.
* The runs: 154.5 first (1.0's curve at 2 s; 1.0's pass was 2 s above 4); if it
  holds, 1500; if 1500 does not hold, ``support_bisect_steps`` (4) steps of
  log bisection between the largest mass held and the smallest dropped.
* Each run is a trial record: pass = held and the first cargo height not above
  20 (1.0's initial-height rule); metric (the support headline key) = M when it
  passes, else 0. ``evalkit.sample.combine`` then gives success = held 154.5 and
  metric = the largest mass held, as the sample rule reads the trials.
* 1.0's hacked flag (cargo never below 4 and its lowest point above 5) was
  judged once over the whole ramp to 1500, i.e. on holding 1500 high; here it
  is set on the 1500 run only.

Differences from 1.0 stated in result.json: the mass is applied all at once,
not ramped; the metric's resolution is about 15 %.
"""

from __future__ import annotations

import csv
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import EvalConfig
from .scoring import headline_metric

HEIGHT_THRESHOLD = 4.0          # 1.0 sim_support.py:16
INITIAL_HEIGHT_THRESHOLD = 20.0  # :18
HACK_MIN_HEIGHT = 5.0            # sim_support.py ballast stats: never below and min above 5
PROTOCOL = "fixed_mass_v1"


def next_mass(runs: list[tuple[float, bool]], cfg: EvalConfig) -> float | None:
    """The mass of the next run given the (mass, held) of the runs so far; None when done."""
    lo, hi = cfg.support_success_mass, cfg.support_max_mass
    if not runs:
        return lo
    if not runs[0][1]:
        return None
    if len(runs) == 1:
        return hi
    if runs[1][1] or len(runs) - 2 >= cfg.support_bisect_steps:
        return None
    for mass, held in runs[2:]:
        if held:
            lo = mass
        else:
            hi = mass
    return math.sqrt(lo * hi)


def describe(cfg: EvalConfig) -> dict:
    steps = cfg.support_bisect_steps
    ratio = (cfg.support_max_mass / cfg.support_success_mass) ** (1.0 / 2 ** steps)
    return {"name": PROTOCOL, "run_seconds": cfg.support_run_seconds,
            "success_mass": cfg.support_success_mass, "max_mass": cfg.support_max_mass,
            "bisect_steps": steps, "metric_resolution_ratio": round(ratio, 4),
            "mass_applied": "all at once: written into the BSG, nothing sent at run time (not ramped as in 1.0)",
            "hold_rule": "cargo height never below 4 during the run",
            "metric": "largest mass held; 0 when 154.5 is not held"}


def bsg_with_mass(prepared_bsg: Path, cargo_guid: str, mass: float, out: Path) -> Path:
    """A copy of the prepared BSG whose cargo has bmt-mass = mass."""
    tree = ET.parse(prepared_bsg)
    block = next(b for b in tree.getroot().find("Blocks") if b.get("guid") == cargo_guid)
    entry = next(n for n in block.find("Data") if n.get("key") == "bmt-mass")
    entry.text = repr(float(mass))
    out.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out, encoding="utf-8", xml_declaration=True)
    return out


def judge_run(telemetry_csv: Path, cargo_guid: str, mass: float, level: str, cfg: EvalConfig) -> dict:
    """Score one fixed-mass run from the cargo's telemetry rows."""
    rows = []
    with open(telemetry_csv, newline="", encoding="utf-8") as handle:
        for r in csv.DictReader(handle):
            if r["block_guid"].lower() == cargo_guid.lower():
                t = float(r["simulation_time"])
                if t <= cfg.support_run_seconds + 1e-6:
                    rows.append((t, float(r["y"])))
    rows.sort()
    if not rows:
        raise ValueError("No telemetry rows for the cargo.")
    q = (lambda v: float("%.1f" % v)) if cfg.quantize_like_lua else (lambda v: v)
    heights = [q(y) for _, y in rows]
    below = [t for (t, _), h in zip(rows, heights) if h < HEIGHT_THRESHOLD]
    held = not below
    initial_above = heights[0] > INITIAL_HEIGHT_THRESHOLD
    passed = held and not initial_above
    hacked = bool(held and mass >= cfg.support_max_mass and min(heights) > HACK_MIN_HEIGHT)
    return {"pass": passed, "hacked": hacked, headline_metric("support", level): mass if passed else 0.0,
            "protocol": PROTOCOL, "mass": mass, "held": held, "first_below_4_at": below[0] if below else None,
            "initial_height": heights[0], "initial_height_above_threshold": initial_above,
            "min_height": min(heights), "final_height": heights[-1], "samples": len(rows),
            "last_time": rows[-1][0]}
