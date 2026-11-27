"""Starship Flight 14 replica: unattended flight software for Super Heavy B21 and Starship S41.

Run through ``run.py`` (rebuild + launch) on LONE ORB. The controller binds named hardware from
parts.py (build IDs) to this run's GUIDs via rebuild_manifest.json, estimates each stage's
rigid-body state from live telemetry and fuel, and flies both stages at once after hot staging.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402

from controller_sdk import ControllerClient, targets_by_guid  # noqa: E402
from controller_sdk.protocol import ENV_MACHINE_BSG, ENV_RUN_DIR  # noqa: E402

from config import CONFIG  # noqa: E402
from flight import Flight  # noqa: E402
from vehicle import Hardware  # noqa: E402


def main() -> int:
    run_dir = Path(os.environ[ENV_RUN_DIR])
    hw = Hardware(run_dir / "rebuild_manifest.json", Path(os.environ[ENV_MACHINE_BSG]))
    cfg = dict(CONFIG)
    # Development hook: SS14DEV_CONFIG names a JSON file of config overrides (the runner only clears the
    # STARSHIP14_ prefix). A run that used it says so in its event log.
    override = os.environ.get("SS14DEV_CONFIG")
    extra = json.loads(Path(override).read_text()) if override else {}
    cfg.update(extra)
    flight = Flight(hw, cfg)
    if extra:
        flight.ev.add(0.0, "dev_overrides", f"Development config overrides applied: {extra}")
    client = ControllerClient.from_environment(poll_interval=0.003)
    client.arm()
    steps = (run_dir / "steps.jsonl").open("w", encoding="utf-8")
    try:
        frame = client.wait_until_running(timeout=120)
        client.load_block_table()
        t0 = frame.simulation_time; w0 = time.time(); last_print = -10.0
        while True:
            t = frame.simulation_time - t0
            d = targets_by_guid(frame, block_guids=client._block_guids)
            cmd = flight.step(t, d)
            client.send_sliders(cmd.slider_list())
            client.send_channels(channels=cmd.keys)
            rec = {"t": round(t, 3), "wall": round(time.time() - w0, 2), "phase": flight.phase,
                   "alive": frame.machine.alive_block_count,
                   "b_power": np.round(flight.b_eng.power, 3).tolist(), "s_power": np.round(flight.s_eng.power, 3).tolist(),
                   "b_phase": flight.booster_gnc.phase, "s_phase": flight.ship_gnc.phase}
            rec.update({k: v for k, v in flight.log.items() if isinstance(v, (dict, float, int))})
            rec["s_rcs"] = np.round(flight.s_rcs.last, 1).tolist(); rec["b_rcs"] = np.round(flight.b_rcs.last, 1).tolist()
            steps.write(json.dumps(rec, default=float) + "\n")
            if t - last_print >= 5.0:
                last_print = t
                print(f"  t={t:6.1f} {flight.phase:9s} B={flight.log.get('booster', '')} S={flight.log.get('ship', '')}", flush=True)
            if flight.done or t > cfg["max_duration"]:
                break
            frame = client.next_sample(timeout=10)
    finally:
        steps.close()
        (run_dir / "flight_events.json").write_text(json.dumps(flight.ev.items, indent=1, default=float), encoding="utf-8")
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
