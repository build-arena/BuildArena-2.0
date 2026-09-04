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
(`fthrust`, `speed`) used by `send_sliders`. There are no name
aliases: prefab strings such as `ReactionSteeringBlock` are dump metadata,
not names, and must not be used to select blocks.

One-command setup is `scripts/setup.ps1`. It requires the published Workshop
item [BuildArena ToolKit](https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349).

Legacy protocol v3 files, split-mod IDs, ToolKit 1.x metadata, and
`tracker.*` keys are rejected explicitly. No compatibility fallback is used.
