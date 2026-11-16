# BuildArena 1.0 tasks on BuildArena 2.0

This directory runs the nine tasks of [BuildArena 1.0](https://github.com/AI4Science-WestlakeU/BuildArena) on BuildArena 2.0. A model builds a machine through the 2.0 MCP tools in a single session. The code here then rebuilds that machine, places it in the game the way 1.0 did, runs it with `besiege_cli`, and scores the 2.0 telemetry with a port of the 1.0 judges.

It has three parts.

| Part | Where |
| --- | --- |
| The nine task prompts and a block registry narrowed to the 1.0 blocks | `tasks/`, `registry/` |
| The port of the 1.0 judges (`analyze/sim_*.py`), with tests against the 1.0 originals | `evalkit/scoring.py`, `evalkit/judge.py`, `evalkit/legacy*.py`, `tests/test_scoring_equivalence.py` |
| Rebuilding and placing the machine, the support banks and the cargo, the transport control agent's prompt and reply parsing, and a step-by-step command line | the rest of `evalkit/` |

The model's build session itself (the agent client that connects the model to the MCP server) is not part of this directory. Section 3 says what a build session has to leave behind for the evaluation.

Words used throughout:

- A **cell** is one category at one level, for example transport Lv.2. 1.0 has three categories (transport, support, lift) and three levels.
- A **sample** is one build session for one cell, and the files it leaves (section 3).
- A **trial** is one run of the sample's machine in the game, scored once. Transport samples get up to three trials, each with a new control sequence. Support and lift samples get one.
- An **attempt** is one evaluation of a sample. Its files go to `<sample>/eval/<attempt>/`.
- The **cargo** is the Ballast block (Besiege's weight block) that 1.0 drops onto transport Lv.2 and Lv.3 machines and onto every support bridge, with a mass that grows during the run.
- The **banks** are the two sides of the gap a support bridge has to span.
- The **valid-only history** is the `<name>.json` the MCP server saves next to `<name>.bsg`: the build operations that succeeded, in order.

The evaluation code was used on 63 samples (105 trials) with Besiege build 24131898 (game version 1.90) on Linux without a display. Six of those samples were evaluated again on build 25434581 (1.91), the version the `main` branch of this repository targets, also on Linux (section 8).

## 1 Quick start

Everything below runs from the repository root. The evaluation needs a checkout that has passed the one-command setup of the [repository README](../README.md#one-command-setup), which also writes the collider dump and `BESIEGE_DATA_PATH` into `.env`, and a clone of BuildArena 1.0. The 1.0 clone supplies the controller prompt and the support level files at run time.

```bash
git clone https://github.com/AI4Science-WestlakeU/BuildArena.git ../BuildArena-1.0
git -C ../BuildArena-1.0 checkout c3005b90686ea6dae9a69a293e7f6395eae77c9c
```

Every Python command adds two packages, pandas and PyYAML, on top of the repository's lock:

```bash
uv run --with-requirements buildarena1/requirements.txt python -m unittest discover -s buildarena1/tests -t .
```

## 2 The tasks and the block set

`tasks/ba1_blocks/<category>_<level>.json` holds one task per cell. The categories are `transport`, `support` and `lift`. The levels are `soft`, `medium` and `hard`, which the 1.0 paper calls Lv.1, Lv.2 and Lv.3. Each file has the build session's system prompt (`system_prompt`), its first user message (`user_message`), the machine name to use (`machine_name`), and the segments both were assembled from.

Most of the text is copied from 1.0's `levels.yaml` and `prompt.yaml`. A copied segment records its source lines and every edit made to it. The edits follow from one difference. 2.0 has no tools to build sub-structures separately and assemble them, so a session builds one machine.

- "Use no more than 3 sub-structures." (support Lv.2 and Lv.3) and "Use only two sub-structures." (lift Lv.3) become "Build everything as one machine in a single build session. Sub-structures cannot be built separately and assembled in this environment."
- "for each sub-structure" is dropped from the two sentences about the starting block.

The new text says how to use the tools and how to end the session. The prompt then lists the 1.0 blocks with their 1.0 descriptions, produced by 1.0's own `Blocks()()`. A last sentence maps three 1.0 names to the 2.0 names: Small Wooden Block is "Single Wooden Block", Powered Wheel is "Wheel", and Winch is "Rope Winch". For transport, the session ends with a `<functionality>` and a `<motion_control>` section, the two parts of a 1.0 plan that the control agent reads.

`registry/ba1_blocks/` is `blocks/` narrowed to the six 1.0 blocks and the Starting Block. The six are Single Wooden Block, Wheel, Water Cannon, Torch, Brace and Rope Winch. The other blocks' `[runtime_defs]` and `[by_id]` sections are removed rather than disabled, because `get_block_description` reads `block_authoring.toml` without looking at the registry. Point the MCP server at the narrowed set with three variables (it reads `block_categories.toml` from the registry's directory):

```bash
BLOCK_REGISTRY_PATH=buildarena1/registry/ba1_blocks/block_registry.generated.toml
BLOCK_AUTHORING_PATH=buildarena1/registry/ba1_blocks/block_authoring.toml
BLOCK_ROLES_PATH=buildarena1/registry/ba1_blocks/block_roles.toml
```

Three scripts keep these files reproducible. They need `BA1_ROOT` set to the 1.0 clone.

```bash
python -m buildarena1.tasks.gen_tasks                 # write the task files from 1.0
python -m buildarena1.tasks.check_tasks               # re-derive every copied segment and compare
python -m buildarena1.registry.gen_registry --check   # the narrowed registry still matches blocks/
```

## 3 What a build session leaves behind

The evaluation reads one directory per build session, called a sample:

```
<runs root>/<batch>/<model label>/<category>_<level>/s<NN>/
```

| File | Needed | Content |
| --- | --- | --- |
| `machine/<name>.bsg` and `machine/<name>.json` | always | The machine the MCP server saved when the session ended, and its valid-only history. There must be exactly one `.bsg`. |
| `tool_calls.jsonl` | when the history does not rebuild the saved machine (section 5) | One JSON object per tool call, in call order: `seq`, `name` (the tool name, optionally prefixed as in `mcp__<server>__<tool>`), `input` (the arguments), `is_error`, and `result`. `result` is the text the tool returned, or the JSON string `{"result": <text>}`. A call that raised has the text `Error executing tool <tool>: <message>`, which is how the MCP server reports it. |
| `final_reply.txt` | transport | The session's last reply, with the `<functionality>` and `<motion_control>` sections. Without them the whole reply goes to the control agent and the sample is flagged. |
| `meta.json`, `usage.json`, `build_check.json` | no | Session status, token use (`input_tokens`, `output_tokens`, `requests`), and the results of checks made by the build side. A check named in `GATE_CHECKS` of `evalkit/sample.py` that is `false` keeps the sample from evaluation. Checks in `FLAG_CHECKS` only flag it. |

The build session should start the MCP server with the narrowed registry, give each sample its own save directory, and not offer `load_machine_from_history` to the model. That tool would load a whole machine from a file.

## 4 Evaluating a sample

A host file names the paths on this machine:

```json
{
  "v1_root": "../BuildArena-1.0",
  "collider_dump": ".local/collider_dump.toml",
  "besiege_data": "<the BESIEGE_DATA_PATH of this checkout's .env>",
  "cargo_plugin": "<path of the installed cargo-mass plugin, or null>"
}
```

Optional keys are `v2_root` (default this checkout), `tasks_dir` (default `buildarena1/tasks/ba1_blocks`), `launch_timeout`, `command_timeout`, `reload_sandbox` and `lock_wait` (seconds a run waits while another run holds the same game, default 1800). Relative paths are taken from the directory the command runs in, the repository root in this README, and are made absolute when the host file is read.

A sample is evaluated in steps. Each step is one call of `python -m buildarena1.evalkit.cli --host HOST.json <step> ...` and prints one JSON object. Its `next` field is the subcommand to call next (`controller-input`, `parse-reply`, `run-trial` or `finalize`), or `done`. The steps keep their state in `<sample>/eval/<attempt>/state.json`.

| Step | What it does |
| --- | --- |
| `prepare SAMPLE [--attempt A] [--config CFG.json]` | Rebuild, place and export the machine. No game is needed. |
| `controller-input SAMPLE --attempt A` | Transport only. Print the control agent's system prompt and messages for this round. |
| `parse-reply SAMPLE --attempt A --reply-file F [--usage-file U]` | Transport only. Take the model's reply (`-` reads stdin) and write the trial's controller. |
| `run-trial SAMPLE --attempt A` | Run the game once and score the telemetry. |
| `finalize SAMPLE --attempt A [--reason TEXT]` | Combine the trials into `result.json`. |

Support and lift go `prepare`, `run-trial`, `finalize`. Transport goes `prepare`, then up to three rounds of `controller-input`, `parse-reply` and `run-trial`, then `finalize`. The command line makes no model call. Between `controller-input` and `parse-reply` the caller sends the printed system prompt and messages to the same model that built the machine, as 1.0 did. A reply without a parsable `json` block is asked again with the same messages, up to three calls. From the second round on, the messages carry 1.0's trajectory feedback from the previous run.

`CFG.json` overrides fields of `EvalConfig` in `evalkit/config.py`, and the full configuration is written into `result.json`. The first trial of a batch freezes the game build, the hashes of both ToolKit copies (the one in the game's `Mods` folder and the Workshop one), the plugin, the code version (the commit and a digest of the current contents of `buildarena1/evalkit/`) and the configuration into `<batch>/eval_fingerprint.json`. Each run also reads from the game's log at `Besiege_Data/output_log.txt` which ToolKit file the game loaded, and fails if its hash is neither of the two. Unity writes the log there on Windows. On macOS it writes it elsewhere, and on Linux too unless the game is started with `-logFile` pointing there. The check is then not made, and `run_meta.json` says so in `toolkit_loaded_check` (`no_game_log`). A later trial that differs is refused, so one batch is never scored under mixed conditions.

`python -m buildarena1.evalkit.cli --host HOST.json summarize <runs root>` prints per-cell success counts, metrics and token use.

## 5 Differences from 1.0

Except for `lift_success_rule` below, the defaults in `evalkit/config.py` are the settings the evaluation above ran with. Most items below can be switched there.

1. **One session, one machine.** 1.0 built each machine with five agents (planner, drafter, reviewer, builder, guidance) and could assemble sub-structures. Here one model builds in one session. In Table 6 of the 1.0 paper the same change halves the success rate on support Lv.1 (45.3% with all roles, 22.6% with the builder alone). Success rates from this setup therefore sit on a different scale from the paper's.
2. **Blocks and their parameters.** Only the six 1.0 blocks and the Starting Block are available (section 2). The 2.0 exporter does not write the parameters 1.0 wrote into the BSG, so they are added after export: Powered Wheel speed 1, acceleration 60, auto-brake on and contact 0.1, and Water Cannon hold-to-fire with strength 4.
3. **Support terrain.** 1.0 ran the support cells on its own level files (`asset/support_{soft,medium,hard}.blv`). 2.0 cannot load them. The game's `-load_level` option takes a built-in scene name, not a file, and in the level editor the ToolKit's `load_machine` fails with `Machine.Active() returned null`. Each of the two terrain blocks of a level file is therefore replaced by one Ballast block in a plain sandbox, scaled to the same 30 x 5 x 20 box at the same position, with mass 100000. The gaps are 5, 10 and 20 as in the level files, and the top of both banks is at height 5.
4. **Cargo mass.** 1.0 raised the cargo's mass from 5 at 0.5 s to 50 over 2 s (transport) or to 1500 over 15 s (support), setting it every physics frame from Lua. In 2.0 the Ballast mass slider stops at 3, and larger values sent at run time are cut to 3. The default mode reads the log of a game plugin that sets the cargo's mass on 1.0's curve (section 6).
5. **Time.** Durations are simulation seconds (30 for transport, 17 for support, 10 for lift Lv.1, 30 for lift Lv.2 and Lv.3). 1.0 timed its runs by the wall clock, and the game runs slower than real time. The game is Besiege 1.9x, not the 1.75 of the paper.
6. **Placement.** Transport and lift machines start 0.1 above their lowest collider point. 1.0 instead pressed the game's Set Ground button, which drops a machine onto the ground. The sandbox scene is `BARREN EXPANSE`, since 1.0 only says "the last sandbox on the right".
7. **Control agent prompt.** The system prompt is `controller.system_message` from 1.0's `prompt.yaml`, with the same block list and name sentence as the build prompt. The 1.0 code at c3005b9 reads `config['agents']['controller']`, which does not exist in that file. Which text 1.0 actually used is therefore not certain. Key presses become 2.0 control channels (`ForwardKey`, `BackwardKey`, `ShootKey`) sent every 0.1 s. When presses of one key overlap, the last one started decides, as in 1.0's Lua loop.
8. **Judging.** The judges follow 1.0 except for the lift success rule below. Times, positions and masses are rounded to 0.1 as 1.0's log did. Two inputs are chosen here. 1.0 asked the game whether a Water Cannon was heated (Lua `burning()`), and here a cannon counts as heated when the 2.0 telemetry field `fire_burning` is set. Lift Lv.2 and Lv.3 are judged on every block except the Starting Block, where 1.0 drew four blocks at random (`lift_tracking = "random4"` in `config.py` gives 1.0's rule with a fixed seed).
9. **Trials and samples.** 1.0 has no code that turns three transport trials into one result. Here a trial counts when it passes and is not marked hacked by 1.0's judge. A sample succeeds when at least one trial counts, and its metric is the largest among the counting trials. The summary also gives, for support, 1.0's mean over all samples with failures as 0.
10. **The machine evaluated.** It is the machine the MCP server saved when the session ended. Two 2.0 tools can change the machine and still raise an error. Removing a connector removes it and then raises, and a shift given as strings that collides is not fully rolled back. A call that raises is in neither history file, so replaying the valid history can give a different machine. The rebuild compares its export block by block with the saved BSG. On a difference it replays the session's calls from `tool_calls.jsonl`, putting back the ones that raised, and checks again. A machine that still differs is not run and gets the status `machine_mismatch`.
11. **Lift success.** For Lv.2 and Lv.3, 1.0 required both `max_height > 30` and `elevation_change > 5`: the peak in `7 < t <= 12` had to exceed the peak in `2 < t <= 7` by more than 5. A fast rocket can reach its highest point before 7 s and descend during the second window, failing that rule despite flying far above 30. The default is now `lift_success_rule = "height"`, which requires only `max_height > 30`. `elevation_change` is still calculated and saved in each trial's `score.json`. To use 1.0's success criterion, pass a configuration file containing `{"lift_success_rule": "ba1"}` to `prepare --config` (section 4). `result.json` records the choice as `config.lift_success_rule`, and the batch fingerprint includes it. Lift Lv.1, transport, support, the hacked rule and the task prompts are unchanged; a passing trial marked hacked still does not count as a success.

Some of these choices were checked in runs on build 24131898 while the code was written. The added wheel and cannon parameters take effect. `fire_burning` marks a heated cannon, while `steam` only marks a cannon firing steam. The floor of `BARREN EXPANSE` is at height 0 to within 0.05. The cargo starts at the position and orientation 1.0 gave it. The banks moved by at most 0.05 in 21 support runs. On build 25434581 the same checks gave the same answers, the banks moved by at most 0.05 in the one support run made there, and the colliders of the seven blocks and the Ballast are the same in both builds.

## 6 The cargo-mass plugin

Transport Lv.2 and Lv.3 and the three support cells need the cargo's mass to follow 1.0's curve. The default mode (`cargo_mass_mode = "plugin_curve"`) relies on a BepInEx plugin in the game ([BepInEx](https://github.com/BepInEx/BepInEx) loads plugins into Unity games). The plugin used for the evaluation above sets only Unity's own Rigidbody mass, does not change game files, and is not included in this repository. Its author can share it on request. Any plugin that does the following works with this code. The two environment variable names are the ones that plugin reads.

- It acts only when the environment variable `BH_CARGO_MASS` is `1`. When simulation starts it reads the JSON file named by `BH_CARGO_MASS_SPEC`. The runner writes that file for every run, with the fields `cargo_guid`, `mass_min`, `mass_max`, `ramp_start`, `ramp_duration`, `clamp_low`, `initial_slider`, `bank_guids` and `log`.
- It finds the cargo's Rigidbody by the BSG GUID `cargo_guid`. Counting the first physics frame of the simulation as frame 1 and taking t = frame / 100, the target mass of a frame is `m(t) = mass_min + (mass_max - mass_min) * max(min((t - ramp_start) / ramp_duration, 1), clamp_low)`, 1.0's curve. Before each physics step, it writes m(t) to the Rigidbody only when the Rigidbody's mass differs from m(t). During the ramp that is every frame. After the ramp it is only when the game has set the mass back (next item).
- It must not write the mass when the Rigidbody already has the target value. Writing the same value every physics frame changes the physics in this game. In one test, the same car with the cargo at its full mass moved 0.178 between 2 s and 7 s when the plugin wrote only changed values, and 31.2 when it wrote the same value every frame, and the cargo slid to the ground. 1.0 set the mass through the game's mass slider, which also changes the Rigidbody only when the value changes.
- The game resets the mass to the BSG value whenever the ToolKit handles a control message, after the plugin's own per-frame update and before the physics step. The plugin therefore checks the mass again late in each frame and writes the target back in the same frame. After each physics step it checks the mass the step used.
- It writes `log` as JSON lines. The first line is `{"event": "start", ...}`. Then comes one line per frame, with at least `frame`, `mass` (the target set for that frame) and `y` (the cargo's height before the step). Per-frame lines have no `event` field, or `"event": "frame"`. Every other line has an `event` field with another value and is not counted as a frame. The last line is `{"event": "stop", "post_step_mismatch": <steps that ran with another mass>, ...}`.

The judge puts the plugin's frames on the telemetry clock by matching `y` with the telemetry heights in the first 2 seconds, and takes the cargo mass of every judged row from the log (`evalkit/cargo_plugin.py`). A run whose log is missing, has no stop record, skips a frame, ends early or reports a post-step mismatch gets no score (`cargo_log_problem` in `evalkit/runner.py`). After the clock offset is found, the log must also reach the frame of the last telemetry sample at that offset, or the trial is `score_failed` (`align` in `evalkit/cargo_plugin.py`).

The game must be started by the run, so that it sees the two variables. `besiege_cli` asks Steam to start the game first wherever it can (always on Windows and macOS, on Linux when `steam` or `xdg-open` is installed), and a game started by Steam does not get the variables. For a run with cargo the runner therefore quits any running game and starts the game binary itself, with the variables, the way `besiege_cli` does when it does not use Steam: `BESIEGE_LAUNCH_COMMAND` if set, else the executable. It waits for the ToolKit and then calls `besiege_cli run`, which uses that game. If the binary cannot be started this way, the run fails before `besiege_cli run` is called. `besiege_cli run` uses the running game when the ToolKit's heartbeat is less than 5 seconds old. If the heartbeat stopped for longer between the two steps, it would start another game, without the variables; this has not been seen, and the trial would then fail for want of a plugin log. On builds 24131898 and 25434581 on Linux, with `BESIEGE_LAUNCH_COMMAND`, the variables reached the game. Starting the executable directly on Windows or macOS with Steam installed has not been tried. If the game hands itself over to Steam there, the plugin writes no log and the trial fails.

**Without the plugin**, set `cargo_plugin` to `null` in the host file. Transport Lv.1 and the three lift cells do not use cargo and run as usual. In the default mode, every run of a cell with cargo stops before the game is started, with an error saying that no plugin is installed. Such a trial ends as `run_failed` and the sample gets no score. Two standby switches run those cells without the plugin, and neither reproduces 1.0's curve. Both were run in the game during development, but no reported score used them.

- `cargo_mass_mode = "fixed_bsg"` writes a fixed cargo mass of 50 into the BSG for transport Lv.2 and Lv.3 and puts the cargo 0.1 above the machine.
- `support_protocol = "fixed_mass_banks"` runs each support sample several times with one fixed mass per run, and a run passes when the cargo never drops below height 4. It runs 154.5 first (1.0's mass at 2 s, the time 1.0's pass rule needs the cargo held), then 1500, then four steps of bisection on a log scale between the largest mass held and the smallest dropped. Adjacent masses it can tell apart differ by about 15%. The whole mass bears on the bridge from the start, so a bridge can fail here and hold under 1.0's slow ramp. Two bridges that held 266 and 465 on the ramp held less than 154.5 and about 237 under this switch.

`result.json` flags samples evaluated in either mode. A third mode, `"slider_clipped"`, sends the curve through the 2.0 mass slider, where the game cuts it to 3.

## 7 Tests

```bash
uv run --with-requirements buildarena1/requirements.txt \
    python -m unittest discover -s buildarena1/tests -t .
```

Without anything else, unittest reports 165 tests, 35 of them skipped. The skipped ones rebuild machines or read 1.0 files, and a test class skipped as a whole is reported as a single skip. To run the 258 tests, set three variables, and add `buildarena1/tests/requirements-v1.txt` (matplotlib, which importing the 1.0 judges needs):

| Variable | Value |
| --- | --- |
| `BA1_ROOT` | the 1.0 clone at c3005b9 |
| `COLLIDER_DUMP_PATH` | the collider dump written by setup (read from `.env` if not set) |
| `BESIEGE_DATA_PATH` | `Besiege_Data`, or any directory with an empty `Skins/` in it (read from `.env` if not set) |

```bash
BA1_ROOT=../BuildArena-1.0 uv run --with-requirements buildarena1/requirements.txt \
    --with-requirements buildarena1/tests/requirements-v1.txt \
    python -m unittest discover -s buildarena1/tests -t .
```

One of them, which checks the stored 1.0 results, is skipped when the 1.0 judges run live.

`test_scoring_equivalence.py` writes synthetic trajectories once as 2.0 telemetry and once as 1.0 Lua log text, and requires the port, with `lift_success_rule = "ba1"`, and 1.0's own `analyze_simulation_<category>` to agree on every result field, floats bit for bit. The cases sit on both sides of every threshold. With `BA1_ROOT` set, 1.0's judges run live, and a `BA1_ROOT` that does not point at a 1.0 clone is an error. A relative `BA1_ROOT` is taken from the directory the tests start in. Without it, the 1.0 side is read from `tests/fixtures/v1_judge_results.json`, which holds 1.0's results on the same trajectories, recorded on Linux x86_64 with pandas 2.3.3 and numpy 2.3.2, and for each run a sha256 of the input the 1.0 judge got. A run whose input no longer has that digest fails, so a stored result is never used for another input. A live run also checks that the stored results and digests still equal 1.0's.

`test_lift_success_rule.py` checks both lift rules on a rocket that peaks at 400 before 7 s and then descends, and on trajectories below and exactly at 30. It also checks that Lv.1 and the hacked rule stay the same, and that the chosen rule is saved in the results.

## 8 Known limitations

- **Where the runner has been run.** `LocalRunner` in `evalkit/runner.py` runs `besiege_cli` from this checkout. It was run on build 24131898 (1.90) on Linux without a display, where `BESIEGE_LAUNCH_COMMAND` started the game under a virtual display. It evaluated eight samples of the evaluation above again: lift Lv.1 and Lv.3, transport Lv.1 twice, support Lv.1 and transport Lv.3 with the cargo-mass plugin, and a support and a transport cell without it. The control agent's replies were the ones stored by the evaluation, given to `parse-reply` in their original order, including one reply without a `json` block that was asked again. Every trial passed or failed as in the evaluation, the prepared machine files were the same byte for byte, the plugin logs passed `cargo_log_problem`, and the cells without the plugin stopped before the game was started. A trial's headline metric differed from the stored one by up to 0.57 (27.64 against 27.07 on transport Lv.3), which is the run-to-run spread described below. The two standby switches and the lock below were also run there. After the runner was changed to start the game itself for runs with cargo, to lock per game and to check the plugin log at the measured offset, a lift Lv.3 sample and a support Lv.1 sample with the plugin were run again with the same results (1258.2 and 465.5), and a run from a second checkout of the same game waited for the lock of the first. The evaluation itself ran the same steps and the same `besiege_cli run` arguments through a runner tied to its own host. On build 24131898 the setup's DLC check fails on block 103 (Fuel Pump), which 1.90 does not have, so block 103 was disabled in `blocks/block_registry.generated.toml` of that checkout. `identity` lists that file under `v2_local_diff_files`. On build 25434581 (1.91), on the same host, an unmodified checkout evaluated six of the same samples again (lift Lv.1 and Lv.3, transport Lv.1 twice, and support Lv.1 and transport Lv.3 with the plugin, built against 1.91) with the same replayed replies. Every sample succeeded as in the evaluation, the prepared machine files were the same byte for byte, and the plugin logs passed both checks. One trial, which had moved 10.26 in the evaluation, moved 9.98 and missed the transport Lv.1 threshold of 10; a second run of that sample on 1.91 moved 10.16 and passed, on a trajectory identical to a 1.90 run. Some 1.91 runs reproduced the evaluation's telemetry byte for byte. On that host the setup's last step, a rocket orbit demo, failed on both builds, after the steps that write what this directory uses had passed. Nothing has been run on Windows or on macOS.
- **Quitting the game.** The runner quits the game with `besiege_cli quit` before every run with cargo and after every run. That command asks the game to quit and, when it does not quit in time, kills every Besiege process of the same user. On an account where someone else may be running Besiege, run the evaluation only when no other game is running. `besiege_cli quit` stops only the game, so a `BESIEGE_LAUNCH_COMMAND` that also starts other processes, such as a virtual display, has to stop them itself when the game exits.
- **The runner's lock is per game and per user, and POSIX only.** It is a file named after the game's `Besiege_Data` path, in `$XDG_CACHE_HOME/buildarena1/locks` (else, or when `XDG_CACHE_HOME` is a relative path, `~/.cache/buildarena1/locks`), outside both the checkout and the game. A second run of the same user on the same game, from any checkout, waits up to `lock_wait` seconds and then returns `game_busy` with the attempt unchanged. Users with different home directories do not share the lock, and on Windows there is none. In those cases run one evaluation at a time.
- **Secondary result fields depend on the platform.** 1.0's judges sort rows with equal timestamps with an unstable sort, so fields such as the maximum speed, and a support hold time within 0.1 s of the 2-second threshold, can differ between machines. Pass and fail and the headline metrics did not change in any trial of the evaluation above that was scored again on a second machine.
- **Runs are not exactly repeatable.** Two runs of the same machine file can end some distance apart, and the transport control agent answers differently each time.
- **Exports differ in the last bits between hosts.** The same machine exported on two hosts can differ by about 1e-14 in positions and rotations, and a connector's Euler angles can come out as a different but equivalent triple. The block comparison of section 5 uses a tolerance of 1e-6 and compares connector rotations as matrices.

## 9 Source and license

The task text, the block descriptions, the controller prompt and the judging logic come from BuildArena 1.0, <https://github.com/AI4Science-WestlakeU/BuildArena>, commit `c3005b90686ea6dae9a69a293e7f6395eae77c9c`, released under CC BY-NC 4.0. The judges in `evalkit/scoring.py` are ports of its `analyze/sim_common.py`, `sim_transport.py`, `sim_support.py` and `sim_lift.py`, and the placement and cargo rules follow its `simulation/simulation_<category>.py` and `spatial/build.py`. Comments in the code cite the 1.0 files and lines the values come from. This directory is released under the repository's license, also CC BY-NC 4.0.
