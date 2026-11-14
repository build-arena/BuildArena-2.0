"""Controller program for ``besiege_cli run --controller controller.py``.

Copied next to a ``schedule.json`` written by evalkit.control. It needs only
the standard library and 2.0's controller_sdk (besiege_cli puts control/ on
PYTHONPATH, run.py:406-410). Every ``tick_s`` of simulation time it publishes
the channels the schedule holds at that time and, when the cell has cargo,
the Ballast ``mass`` slider from the 1.0 ramp (1.0 spatial/build.py:1289-1292).
It stops at ``duration`` simulation seconds. A trace of what it sent goes to
controller_trace.jsonl in the run directory (each record also has the wall
clock, so simulation speed can be read off, and the action sequence number;
"mass" is the cargo mass actually sent after the game's slider range clipped
it, "mass_ramp" the 1.0 ramp value), and the runtime block table the
game published, with every slider's range and initial value, is copied to
block_table_snapshot.json there.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import time
from pathlib import Path

EPS = 1e-4


def load_schedule(path: Path | None = None) -> dict:
    path = path or Path(__file__).with_name("schedule.json")
    schedule = json.loads(path.read_text(encoding="utf-8"))
    if schedule.get("schema") != "buildarena1.control_schedule.v1":
        raise ValueError(f"Unexpected schedule schema {schedule.get('schema')!r}")
    return schedule


def held_channels(schedule: dict, t: float) -> list[tuple[str, str, float]]:
    """Channels held at simulation time t, as (guid, channel, 1.0)."""
    out = []
    for item in schedule["channels"]:
        if item["start"] - EPS <= t < item["end"] - EPS:
            out.append((item["guid"], item["channel"], 1.0))
    return out


def cargo_mass(ballast: dict, t: float) -> float:
    """The 1.0 Lua ramp; mirrors evalkit.legacy.MassRamp.mass_at."""
    frac = max(min((t - ballast["ramp_start"]) / ballast["ramp_duration"], 1.0), ballast["clamp_low"])
    mass = ballast["mass_min"] + (ballast["mass_max"] - ballast["mass_min"]) * frac
    return mass if ballast.get("cap") is None else min(mass, ballast["cap"])


def next_tick(t: float, tick_s: float) -> float:
    return (math.floor(t / tick_s + EPS) + 1) * tick_s


def main() -> int:
    from controller_sdk import ControllerClient
    from controller_sdk.protocol import ENV_RUN_DIR

    schedule = load_schedule()
    ballast = schedule["ballast"]
    acts = bool(schedule["channels"]) or ballast is not None
    trace_path = Path(os.environ[ENV_RUN_DIR]) / "controller_trace.jsonl"
    wall0 = time.monotonic()
    client = ControllerClient.from_environment(poll_interval=0.01)
    client.arm()
    # Prestart handshake (besiege_cli run --controller-prestart): physics starts
    # only after this file exists, so the first sample below is the first tick.
    ready_path = os.environ.get("BUILDARENA_CONTROLLER_READY")
    if ready_path:
        from controller_sdk.protocol import ENV_RUN_ID, atomic_write_json
        atomic_write_json(Path(ready_path), {"schema": "buildarena.controller_ready.v1",
                                             "run_id": os.environ[ENV_RUN_ID]})
    sample = client.wait_until_running(timeout=120.0)
    if client._block_guids is None:
        client.load_block_table()
    run_dir = Path(os.environ[ENV_RUN_DIR])
    try:
        shutil.copyfile(client.block_table_path, run_dir / "block_table_snapshot.json")
    except (OSError, AttributeError) as exc:   # evidence only; the run goes on
        (run_dir / "block_table_snapshot.error").write_text(str(exc), encoding="utf-8")
    mass_range, slider = None, None
    if ballast is not None:
        try:
            slider = next((sl for sl in client.sliders if sl.block_guid_lower == ballast["guid"].lower()
                           and sl.name == "mass"), None)
        except (AttributeError, RuntimeError):   # no published slider table: record the ramp as sent
            slider = None
        mass_range = None if slider is None else (slider.minimum, slider.maximum)
    due = 0.0
    with trace_path.open("w", encoding="utf-8") as trace:
        try:
            while float(sample.simulation_time) < schedule["duration"]:
                t = float(sample.simulation_time)
                if acts and t + EPS >= due:
                    held = held_channels(schedule, t)
                    seq = client.send_channels(channels=held)
                    record = {"t": t, "wall": round(time.monotonic() - wall0, 3),
                              "held": [[g, c] for g, c, _ in held], "action_seq": seq}
                    if ballast is not None and ballast.get("fixed_mass") is not None:
                        record["mass"] = ballast["fixed_mass"]   # written into the BSG; nothing sent
                    elif ballast is not None:
                        ramp = cargo_mass(ballast, t)
                        record["mass_ramp"] = ramp
                        if ballast.get("unclipped") and slider is not None:
                            # The 1.0 Lua set the mass slider straight to 50 / 1500
                            # (spatial/build.py:1289-1292). send_sliders would clip to the
                            # published slider range (client.py BlockSlider.clip), so the
                            # value goes out through the client's own record path unclipped.
                            record["action_seq"] = client._send_merged(sliders=[(slider.index, float(ramp))],
                                                                       keepalive=False)
                            record["mass"] = ramp
                        else:
                            record["action_seq"] = client.send_sliders([(ballast["guid"], "mass", ramp)])
                            # send_sliders clips to the slider range the game publishes (1.90: Ballast
                            # mass 0.2..3), so "mass" is the value actually sent.
                            record["mass"] = ramp if mass_range is None else min(max(ramp, mass_range[0]),
                                                                                 mass_range[1])
                    trace.write(json.dumps(record) + "\n")
                    due = next_tick(t, schedule["tick_s"])
                sample = client.next_sample(timeout=10.0)
        finally:
            client.close()
            try:   # the table as the game publishes it at the end, to compare slider values
                shutil.copyfile(client.block_table_path, run_dir / "block_table_end.json")
            except (OSError, AttributeError):
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
