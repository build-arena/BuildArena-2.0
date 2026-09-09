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

Build history is the authoritative record of construction; `.bsg` is its export.
`inspect-machine` replays the paired history to describe the structure and uses
the BSG for saved control addressing. Pairing checks order, count, type and name;
it does not prove that an externally edited BSG still matches the history.
Re-export from history if the files diverged.

Reports start with an overview, then build structure, controls, warnings and
optional details. Build IDs identify authoring objects. SDK/timeline examples
explicitly label their BSG-index selectors; those are not build IDs or runtime
action indices. `--verbose` adds full captions, faces, saved fields, export GUIDs
and KeyList bindings. `--out` exports a channel map, not the text report.
Positions and directions reuse the builder's `Vector.coordinates`,
`Orientation.caption`, block descriptors and spin descriptions: East/West,
North/South, Up/Down and compass angles. No quaternion decoding is needed to
read a block's orientation. See [presentation examples](../docs/tool-text-presentation.md).

Channel names are the catalog semantic names
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

## Initialize a controller before physics

For machines that cannot stand without feedback during Python startup, use
`run --controller-prestart --pre-controller-hold 0`. This opt-in mode starts
the Python subprocess before simulation and requires a readiness handshake.
Timeline controllers and nonzero pre-controller holds are rejected.

Finish imports/model initialization, create and arm the SDK client, then
atomically write the following object to the path in
`BUILDARENA_CONTROLLER_READY` (only set in prestart mode):

```python
ready_path = os.environ.get("BUILDARENA_CONTROLLER_READY")
if ready_path:
    atomic_write_json(Path(ready_path), {
        "schema": "buildarena.controller_ready.v1",
        "run_id": os.environ["BUILDARENA_RUN_ID"],
    })
frame = client.wait_until_running(timeout=60)
channels = client.load_block_table()
```

Use `atomic_write_json` from `controller_sdk.protocol`. Runtime block tables
are available after simulation starts; do not wait for them before the
handshake. The runner checks the run ID and schema, waits at most 15 seconds
for readiness within the controller timeout, and terminates the subprocess
if readiness or simulation startup fails. This removes process initialization
from the uncontrolled physics interval; it does not guarantee real-time
telemetry delivery or a stable gait.

## Telemetry contention and deadlines

`read_sample(timeout=0.05)` and `read_bulk_batch(timeout=0.05)` return a
consistent commit or raise `SnapshotUnavailableError` for transient publication
contention. They retain marker/buffer/sequence validation and use bounded
backoff. A marker or buffer caught mid-rewrite (empty, or not yet decodable)
is retried within the read budget; an empty file at the deadline is reported as
`SnapshotUnavailableError`, while data that still does not decode at the
deadline raises the codec error itself. Permanent I/O errors fail immediately.
A changed marker is checked before decoding an abandoned buffer; it is not
evidence that the current committed frame is corrupt.

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


## Reading telemetry and control values

Keep these sources separate:

- Build configuration: authored settings reconstructed from history.
- Export configuration: fields saved in the BSG; not a live measurement.
- Catalog metadata: channel descriptions and slider ranges/defaults. Unknown
  entries remain unknown; a default is not the saved or effective value.
- Runtime metadata: the loaded block table, including available slider ranges
  and `initial_value` when published. Initial values are not continuous readings.
- Control request: values sent by the controller. `sequence_applied` reports
  protocol application; it does not establish physical success.
- Telemetry observation: valid values in a received frame. A command target or
  a catalog description cannot substitute for a missing observation.

Frame identity and availability:

- `sequence`: telemetry publication sequence; a repeated frame is not a new sample.
- `episode`: simulation episode identity. Keep histories and derivatives separate
  across episode changes.
- `simulation_time`: simulation time in seconds, not wall-clock time.
- `field_mask` / `machine_field_mask`: included target/machine fields. Inclusion
  does not guarantee that every target has a valid reading.
- `valid` and optional `*_valid` flags: check before using values; invalid storage
  values must not be interpreted as measured zero.
- `receipt_age_seconds`: local elapsed time since receipt of a distinct frame;
  not game-to-controller latency. The caller defines acceptable staleness.

Target fields are `position`, `rotation`, `velocity`, `angular_velocity`,
`fuel`, `fire`, `steam`, `ice`, `health` and `buoyancy`. Machine fields are
`machine_integrity` and `alive_block_count`. The `full` profile enables all;
`position-only` enables position alone. Optional resource/condition fields have
field-specific validity flags. The codec defines record layout, not physical
calibration: do not infer SI units, physical joint limits or applicability from
field names. Where the installed producer's contract does not establish units
or reference frames, those semantics are unknown.

Telemetry arrays retain the producer's format. The builder's compass renderer
operates on authoring vectors; do not feed telemetry arrays into it without an
established frame/component-order conversion. No conversion or new physical
observation is introduced by this presentation change.
