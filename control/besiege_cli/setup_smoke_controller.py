"""Live controller used by one-command setup to prove all-block telemetry works.

Holds the assembled validation machine for HOLD_SECONDS of BAT4 simulation
time. Block destruction is a lifecycle event: omitted GUIDs and a dropping
alive_block_count are recorded, not treated as failure.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from controller_sdk import ControllerClient, targets_by_guid
from controller_sdk.protocol import ENV_RUN_DIR

REQUIRED_FRAMES = 5
HOLD_SECONDS = 15.0
PROGRESS_EVERY = 2.0
EVIDENCE_NAME = "smoke_evidence.json"


def _finite_numbers(value: object) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, tuple):
        return all(_finite_numbers(item) for item in value)
    return False


def _machine_snapshot(frame) -> dict[str, object]:
    machine = frame.machine
    return {
        "machine_integrity": machine.machine_integrity,
        "alive_block_count": machine.alive_block_count,
        "simulation_time": float(frame.simulation_time),
        "sequence": int(frame.sequence),
    }


def main() -> int:
    client = ControllerClient.from_environment()
    run_dir = os.environ.get(ENV_RUN_DIR, "")
    if run_dir.strip() == "":
        raise RuntimeError(f"{ENV_RUN_DIR} is not set; setup smoke cannot write evidence.")
    evidence_path = Path(run_dir) / EVIDENCE_NAME

    client.arm()
    try:
        first = client.wait_until_running(timeout=60.0)
        if client._block_guids is None:
            client.load_block_table()
        if not client._block_guids:
            raise RuntimeError("block_table.json published no target GUIDs.")
        block_guids = dict(client._block_guids)
        seen: dict[str, dict[str, object]] = {}
        sequences = [int(first.sequence)]
        frames = [first]
        last_report = float(first.simulation_time)
        stop_reason = "hold_reached"
        print(
            f"All-block smoke holding for {HOLD_SECONDS:.1f}s of simulation time "
            f"(need at least {REQUIRED_FRAMES} BAT4 frames).",
            flush=True,
        )

        while True:
            latest = frames[-1]
            elapsed = float(latest.simulation_time) - float(first.simulation_time)
            if elapsed >= HOLD_SECONDS and len(frames) >= REQUIRED_FRAMES:
                break
            try:
                nxt = client.next_sample(timeout=5.0)
            except TimeoutError as exc:
                stop_reason = f"telemetry_stopped: {exc}"
                print(
                    f"BAT4 stopped after {elapsed:.2f}s of simulation "
                    f"({len(frames)} frames). {exc}",
                    flush=True,
                )
                break
            frames.append(nxt)
            sequences.append(int(nxt.sequence))
            if float(nxt.simulation_time) - last_report >= PROGRESS_EVERY:
                snapshot = _machine_snapshot(nxt)
                held = float(nxt.simulation_time) - float(first.simulation_time)
                print(
                    f"  t={float(nxt.simulation_time):6.2f}s "
                    f"held={held:6.2f}s "
                    f"seq={int(nxt.sequence)} "
                    f"alive={snapshot['alive_block_count']} "
                    f"integrity={snapshot['machine_integrity']}",
                    flush=True,
                )
                last_report = float(nxt.simulation_time)

        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            raise RuntimeError(f"BAT4 sequences were not strictly increasing: {sequences}")

        for frame in frames:
            targets = targets_by_guid(frame, block_guids=block_guids)
            for guid, values in targets.items():
                if not values:
                    raise RuntimeError(f"Target {guid} produced an empty telemetry row.")
                for field, field_value in values.items():
                    if not _finite_numbers(field_value):
                        raise RuntimeError(
                            f"Target {guid} field {field!r} is not finite: {field_value!r}."
                        )
                seen[guid] = {
                    "fields": sorted(values),
                    "x": values.get("x"),
                    "y": values.get("y"),
                    "z": values.get("z"),
                }
        table = [block_guids[index] for index in sorted(block_guids)]
        omitted = sorted(set(guid.lower() for guid in block_guids.values()) - set(seen))
        if not seen:
            raise RuntimeError("No live (valid=1) telemetry sample in the collected frames.")

        first_machine = _machine_snapshot(first)
        last_machine = _machine_snapshot(frames[-1])
        hold_elapsed = float(frames[-1].simulation_time) - float(first.simulation_time)
        if hold_elapsed + 1e-6 < HOLD_SECONDS and stop_reason == "hold_reached":
            stop_reason = "hold_short_without_timeout"

        payload = {
            "schema": "buildarena.setup_smoke_evidence.v1",
            "required_frames": REQUIRED_FRAMES,
            "hold_seconds": HOLD_SECONDS,
            "hold_elapsed": hold_elapsed,
            "stop_reason": stop_reason,
            "sequences": sequences,
            "block_table_guids": table,
            "sampled_guids": sorted(seen),
            "omitted_guids": omitted,
            "first_machine": first_machine,
            "last_machine": last_machine,
            "blocks_lost": (
                None
                if first_machine["alive_block_count"] is None
                or last_machine["alive_block_count"] is None
                else int(first_machine["alive_block_count"]) - int(last_machine["alive_block_count"])
            ),
            "targets": seen,
        }
        evidence_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(
            f"Smoke hold finished: elapsed={hold_elapsed:.2f}s frames={len(frames)} "
            f"stop_reason={stop_reason} omitted={len(omitted)} "
            f"blocks_lost={payload['blocks_lost']}",
            flush=True,
        )
        if hold_elapsed + 1e-6 < HOLD_SECONDS:
            raise RuntimeError(
                f"All-block smoke ended after {hold_elapsed:.2f}s of simulation; "
                f"needed {HOLD_SECONDS:.1f}s. stop_reason={stop_reason}. "
                "This is an early exit (often the game stopping after block loss), "
                "not a successful hold."
            )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
