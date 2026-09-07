# BuildArena Control

This directory is the public Python interface for running and controlling
Besiege machines with BuildArena ToolKit 2.0.9.

## Requirements

- Python 3.12+ and `uv`
- Besiege with the verified BuildArena ToolKit 2.0.9 Release enabled
- `BESIEGE_DATA_PATH` set to the game's `Besiege_Data` directory
- A channel catalog produced or verified by `scripts/setup.ps1`, placed at
  `blocks/block_channel_catalog.json` or passed with `--catalog`

Install dependencies from the repository root:

```powershell
uv sync
```

Run commands from this directory:

```powershell
cd control
uv run python -m besiege_cli --help
```

## Example machine

[`examples/Reusable_Heavy_Launcher.json`](./examples/Reusable_Heavy_Launcher.json)
is the repository example build-history. Rebuild it, then fly the closed-loop
orbit mission on LONE ORB:

```powershell
uv run python ..\scripts\rebuild_from_record.py `
  --record-json examples\Reusable_Heavy_Launcher.json `
  --machine-name Reusable_Heavy_Launcher `
  --replace

uv run python -m besiege_cli run `
  --bsg "$env:SAVED_MACHINE_DIR\Reusable_Heavy_Launcher\Reusable_Heavy_Launcher.bsg" `
  --controller examples\reusable_heavy_launcher_orbit.py `
  --sandbox "LONE ORB" `
  --telemetry-hz 10 `
  --telemetry-profile full
```

One-command setup runs this rebuild-and-orbit loop as its last in-game demo.

## Run a controller

Live Python controller (orbit mission):

```powershell
uv run python -m besiege_cli run `
  --bsg machine.bsg `
  --controller examples\reusable_heavy_launcher_orbit.py `
  --sandbox "LONE ORB" `
  --telemetry-hz 10
```

Timeline controller (`buildarena.control_timeline.v2`):

```json
{
  "schema": "buildarena.control_timeline.v2",
  "events": [
    {"time": 0.5, "block": 1, "channel": "ThrustKey", "value": 1.0}
  ]
}
```

```powershell
uv run python -m besiege_cli run `
  --bsg machine.bsg `
  --controller timeline.json `
  --sandbox "BARREN EXPANSE"
```

The CLI validates the machine and controller before launch, binds every run to
a unique run ID, and writes outputs under `datacache/runs/`.

## Useful commands

```powershell
uv run python -m besiege_cli inspect-machine --bsg machine.bsg
uv run python -m besiege_cli configure-telemetry --bsg machine.bsg --all-targets
uv run python -m besiege_cli convert-recording --help
uv run python -m telemetry merge-telemetry --help
```

`inspect-machine` is the control-address source of truth. Each block line is
the unique authored name from `blocks/block_authoring.toml` plus numeric
`id=` (the game join key). Channel names are the catalog semantic names
(`ThrustKey`, `LeftKey`) used by `send_channels`. Slider names are the
authored `MSlider.Key` values in `blocks/control_descriptors/`
(`fthrust`, `speed`) used by `send_sliders`. Block names have no aliases:
prefab strings such as `ReactionSteeringBlock` are dump metadata and must
not be used to select blocks. Control channels accept only their canonical
name, explicit descriptor aliases, and `keylist_<index>` position addresses.
Names are case-sensitive; `leftKey` on a hinge and `LeftKey` on a reaction
wheel are distinct per-block contracts.

## Control name binding

[`keylist_bindings.py`](../blocks/control_descriptors/keylist_bindings.py)
declares verified KeyList positions explicitly. Description order and keyboard
remapping cannot change these addresses. Descriptions without verified slots
are not exposed as controls. Catalog generation and loading reject missing,
extra, duplicate, or conflicting declared slots; re-probe the installed game
and update the declaration if its layout changes.

Each CLI run writes `control_bindings.json` in its run directory and passes
`BUILDARENA_CONTROL_BINDINGS` to the controller. `ControllerClient.from_environment()`
joins this run-bound mapping to `block_table.json` by GUID, local block index,
and KeyList index. The game-published action index remains authoritative.
Missing channels, conflicting runtime names, stale run IDs, and unsupported
binding versions fail before the controller sends actions. `client.channels`
then exposes the same canonical names as `inspect-machine` and timeline commands.

For a standalone SDK client, pass `bindings_path=...` with a binding file
generated for that run. Without it, the SDK exposes only runtime-published
names and position addresses; it cannot reconstruct block types from the
current v4 block table. BSG/catalog files are not runtime address sources.

## Telemetry contention and deadlines

`read_sample(timeout=0.05)` and `read_bulk_batch(timeout=0.05)` return a
consistent commit or raise `SnapshotUnavailableError` for transient publication
contention. They retain marker/buffer/sequence validation and use bounded
backoff. Stable invalid payloads, unsupported versions, and permanent I/O
errors fail immediately. A changed marker is checked before decoding an
abandoned buffer; it is not evidence that the current committed frame is corrupt.

`next_sample`, `next_bulk_batch`, `wait_until_running`, and `wait_until_applied`
retry contention within their own overall deadline. Controllers should use
these waiting APIs instead of matching error strings or stacking fresh
timeouts. Old frames are never returned as new samples. A controller must
handle prolonged telemetry loss according to its own control policy.

`client.telemetry_read_stats` exposes retries, unavailable read windows, last
and maximum read durations, and `receipt_age_seconds` since receiving a distinct
frame. This age measures local freshness, not game-to-controller latency.
`close()` sends an empty action snapshot, waits for acknowledgement through
transient contention, and always disarms in `finally`. A failed acknowledgement
still raises after disarming; it must not be reported as confirmed game release.

Run the protocol and binding regression tests from the repository root:

```powershell
.venv/Scripts/python.exe -m unittest discover -s control/tests -v
```

One-command setup is `scripts/setup.ps1`. It requires the published Workshop
item [BuildArena ToolKit](https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349).

Legacy protocol v3 files, split-mod IDs, ToolKit 1.x metadata, and
`tracker.*` keys are rejected explicitly. No compatibility fallback is used.
