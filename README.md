<div align="center">

<a href="https://build-arena.github.io/ConstructionChallenge/">
  <img src="./docs/assets/challenge_title.svg" alt="BuildArena 2.0 — Construction Challenge" width="640" />
</a>

**From machine design to autonomous execution.**

[Construction Challenge](https://build-arena.github.io/ConstructionChallenge/) · [BuildArena 1.0](https://build-arena.github.io/) · [中文 README](./docs/README.zh-CN.md)

</div>

## Welcome, User & Agent

BuildArena 2.0 is an engineering construction arena where you build with AI
agents in Besiege, a physics sandbox for land, sea, and space machines.
It gives AI agents an end-to-end engineering workflow in
[Besiege](https://store.steampowered.com/app/346010/_/): **automated game launch
and execution, machine construction, closed-loop control, telemetry, and timeline
replay**, with a Python interface and **MCP tools**. Powered by
[BuildArena ToolKit](https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349)
and the Besiege CLI, agents can build a machine, run it, observe its behavior, and
control it in simulation.

This repository connects to **your installed Steam copy of the game**. It needs
the game's block geometry, DLC content, and ToolKit to build and test machines;
the repository does not include the game, DLC, or Workshop mod.

- **[Construction Challenge](https://build-arena.github.io/ConstructionChallenge/)**: tracks, scoring, rules, and submissions.
- **[BuildArena 1.0 (ICML 2026)](https://build-arena.github.io/)**: the original benchmark, paper, and project.
- **[Control guide](./control/README.md)**: run commands, controllers, telemetry, and replay.

## Before setup: complete the prerequisites

**Required order: prepare the game and MOD → pass one-command setup → run examples.**
These examples run real simulations in your installed game. Cloning the repository
or installing Python dependencies alone is not enough to reproduce them.

“Prerequisites complete” means **all** of the following are ready:

1. Use **Windows 10 or 11**, **Linux**, or **macOS**, with Steam installed and signed in.
2. Own a **licensed Steam copy** of [Besiege](https://store.steampowered.com/app/346010/_/)
   and **both DLC**: [The Splintered Sea](https://store.steampowered.com/app/2165710/Besiege_The_Splintered_Sea/)
   and [The Broken Beyond](https://store.steampowered.com/app/3639470/Besiege_The_Broken_Beyond/).
   **Install all three and wait for Steam downloads/updates to finish.** Ownership
   alone is not enough; the DLC supply the water and space blocks used here.
3. Subscribe to the specified Workshop MOD,
   [BuildArena ToolKit](https://steamcommunity.com/sharedfiles/filedetails/?id=3795335349)
   (**item 3795335349**), and **wait for Steam to download it**. A subscription
   without downloaded files is not ready. Disable retired Controller, Block Tracker,
   Collider Dumper, and Block Inspector mods; use the current ToolKit.
4. Confirm Besiege launches from Steam. If you are new to the game, spend a few
   minutes entering a sandbox, loading a machine, and starting/stopping simulation.

**Only then proceed to one-command setup.** Setup automates the local configuration
and validation; it does not purchase or install Besiege/DLC or subscribe on your behalf.

## One-command setup

**Only after completing the prerequisites above**, run this from the repository
root. On Windows 10 or 11, use PowerShell:

```powershell
uv run python scripts/setup.py
```

**No [uv](https://docs.astral.sh/uv/) installed?** Use the wrapper, which installs `uv`, runs `uv sync`,
and launches the same setup script:

```powershell
powershell -ExecutionPolicy ByPass -File scripts\setup.ps1
```

On macOS or Linux, from the repository root:

```bash
uv run python scripts/setup.py
```

The Unix wrapper installs `uv` when it is missing, then runs the same script:

```bash
bash scripts/setup.sh
```

Setup configures local paths, enables ToolKit, collects/verifies block data,
generates the control catalog, and runs two in-game tests: an all-block telemetry
smoke test and the rocket orbit-and-return mission. It then writes `mcp.json`.
**Setup is complete only when the command exits successfully and
`.local/setup-report.json` records `status=passed`.** If it reports `blocked` or
`failed`, resolve the reported issue and rerun setup before running examples.

### Non-default game location

Provide the game-data path if setup cannot find your Besiege installation.
On Windows and Linux that directory is **`Besiege_Data`**:

```powershell
uv run python scripts/setup.py --besiege-data "D:\SteamLibrary\steamapps\common\Besiege\Besiege_Data"
```

With the Windows wrapper, use `-BesiegeData "D:\SteamLibrary\steamapps\common\Besiege\Besiege_Data"`.
On Linux:

```bash
uv run python scripts/setup.py --besiege-data "$HOME/.local/share/Steam/steamapps/common/Besiege/Besiege_Data"
bash scripts/setup.sh --besiege-data "$HOME/.local/share/Steam/steamapps/common/Besiege/Besiege_Data"
```

On macOS the data root is **`Besiege.app/Contents`** (it contains `Skins`; there is no `Besiege_Data` folder):

```bash
uv run python scripts/setup.py --besiege-data "$HOME/Library/Application Support/Steam/steamapps/common/Besiege/Besiege.app/Contents"
bash scripts/setup.sh --besiege-data "$HOME/Library/Application Support/Steam/steamapps/common/Besiege/Besiege.app/Contents"
```

To locate the install, use **Steam → Besiege → Manage → Browse local files**.

Rerun setup after resolving a missing prerequisite. It checks actual artifacts
before continuing.

## Run three automatic machine examples

**Run these only after [one-command setup](#one-command-setup) passes**
(`.local/setup-report.json`: `status=passed`). From the repository root, run
**one example at a time** and wait for it to finish before starting the next.
The PowerShell blocks below are the Windows form; macOS and Linux run the same
`uv run python ...` commands in a terminal.
Each command rebuilds its final machine from **one MCP tool-history JSON**, then
launches the game and runs the complete controller:

```powershell
# Heavy rocket: reach orbit, complete one revolution, and return to land
uv run python control/examples/rocket_orbit_return/run.py

# Transforming car: drive, take off, perform aerobatics, land, and drive on
uv run python control/examples/transforming_car_aerobatics/run.py

# Shuttle: orbital flight, twin-booster return, and glide recovery
uv run python control/examples/shuttle_booster_recovery/run.py
```

Each example directory contains only `machine.json` and its Python launch/control
code. The shuttle's four hinge limits and three blade flips are applied before
simulation; its geometry comes entirely from the history. Rebuilt machines,
calibration bindings, logs and telemetry go to the Git-ignored `datacache/manual_cases/`.
Setup itself uses the same rocket orbit-and-return Python runner.
The car runner re-enters the sandbox before loading the machine to clear stale
building objects. This happens before the optional camera-editing pause.

**Place cameras and record manually:** append `--edit-before-start` to any command.
After the machine loads in build mode, add cameras, **Save As** the name printed
in the terminal, then type `yes`. For an explicit save name:

```powershell
uv run python control/examples/rocket_orbit_return/run.py --edit-before-start --camera-bsg Rocket_orbit_camera
# Next recording: reuse cameras without the editing pause
uv run python control/examples/rocket_orbit_return/run.py --camera-bsg Rocket_orbit_camera
```

Use OBS or another external recorder and the game's camera controls; these
scripts do not enable CLI screen recording or camera follow. Rebuilding displays
a tqdm progress bar; simulation starts without a countdown or recording reminder.
The default tail is 20 simulation seconds after controller completion.
Use `--tail-seconds` or `--prepare-only` as needed.
The latter only rebuilds, without loading or simulating in the game.

## Diagnostics

```powershell
uv run python -m buildarena.paths
```

This reports configured and missing paths, ToolKit installation, and setup status.
Check `.local/setup-report.json` for the validation result; a successful setup
records `status=passed`.

## Manual setup fallback

Use these steps if you need to configure paths or troubleshoot individual stages.
Inspector initialization and in-game validation still use the setup script.

1. **Prepare the host and Steam.** On Windows 10 or 11, Linux, or macOS, install the game, both DLC, and the ToolKit
   Workshop item listed above. Remove or disable retired Controller, Block
   Tracker, Collider Dumper, and Block Inspector mods so only the current ToolKit
   is active.
2. **Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then
   prepare the environment** from the repository root:

   ```powershell
   uv sync
   Copy-Item .env.example .env
   ```

   Copy the template only if `.env` does not already exist.
3. **Set the local paths in `.env`**, adjusting the game location as needed:

   ```dotenv
   BESIEGE_DATA_PATH=C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data
   SAVED_MACHINE_DIR=C:\Program Files (x86)\Steam\steamapps\common\Besiege\Besiege_Data\SavedMachines\BuildArena
   COLLIDER_DUMP_PATH=.local/collider_dump.toml
   ```

   Create the `SavedMachines\BuildArena` folder if needed. Relative paths resolve
   from the repository root; the collider dump is generated in the next step.
4. **Enable ToolKit in Besiege's mod loader**, then run
   `uv run python scripts/setup.py` to initialize Inspector, collect artifacts,
   and validate the control stack. Do not use the retired block-clicking or
   `block_preview.ipynb` workflow. Run the diagnostics command above to verify.
5. **Connect your agent through MCP** using the configuration below.

For older checkouts, setup does not migrate legacy generated configuration.
Clear obsolete setup artifacts before rerunning; preserve `datacache/` and
machine records in `.local/Machine/`.

## Connect through MCP

Setup generates **`mcp.json`** with this checkout's absolute path. Import it into
your agent's MCP configuration, or use [`mcp.example.json`](./mcp.example.json)
and replace the repository path:

```json
{
  "mcpServers": {
    "build-arena": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\you\\path\\to\\BuildArena-2-0",
        "python",
        "-m",
        "buildarena.mcp_server"
      ]
    }
  }
}
```

For direct CLI access and control examples, see [`control/README.md`](./control/README.md):

```powershell
cd control
uv run python -m besiege_cli --help
```

## Challenge outputs and submission

Keep all three required build artifacts:

| Artifact | Purpose |
| --- | --- |
| Raw `.bsg` machine | The runnable machine. |
| Valid-action history JSON | Effective structural updates used to rebuild the machine. |
| Full-action history JSON | Complete history, including queries and rollback/error-recovery traces. |

Game-side outputs live in `SAVED_MACHINE_DIR` and the ToolKit data folder under
`Besiege_Data/Mods/Data/`. Build records are also retained in `.local/Machine/`.

**Do not manually modify the generated machine structure.** Structural edits can
invalidate a [Construction Challenge submission](https://build-arena.github.io/ConstructionChallenge/).
You may load, inspect, and drive the machine and tune control parameters.

To recover an original machine, restore its records from `.local/Machine/` or
rebuild from its saved operation history:

```powershell
uv run python scripts/rebuild_from_record.py --record-json ".local/Machine/<machine>/<machine>.json"
```

Rebuilding creates a new timestamped machine output.

## Render a build-history animation

Render one valid-only history JSON into a continuously orbiting build animation,
with automatic background deflicker, retimed step subtitles, resumable PNG frames,
and full MP4 validation:

```powershell
uv run --extra render python scripts/render_history.py control/examples/rocket_orbit_return/machine.json
```

Use **Blender 5.1.1** with BesiegeCreationImporter source commit
**`c2c2b8b5d1171c03aee05c2f888c394aa3775345`** and configured game assets.
Blender, importer source, and game assets are **not bundled in this repository**:
install Blender separately; the renderer fetches the pinned importer into the
git-ignored `.local/` cache. Defaults to 1080p/30 fps.
See [exact versions, installation and offline setup](docs/rendering.md).
