"""uv run python -m besiege_cli <command> ...

Unified Besiege controller runner. The main entry point is `run`, which
takes exactly three business inputs (machine, controller, sandbox) and does
everything else automatically:

    uv run python -m besiege_cli run --bsg machine.bsg --controller policy.py   --sandbox "BARREN EXPANSE"
    uv run python -m besiege_cli run --bsg machine.bsg --controller timeline.json --sandbox "BARREN EXPANSE"

The remaining commands are low-level diagnostics/authoring helpers (launch,
enter-sandbox, load-machine, start-sim, stop-sim, quit, dump-channels,
inspect-machine, configure-telemetry, convert-recording); they are not
needed for a normal run.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from .key_events import read_key_events
from .machine import (
    configure_telemetry_bsg,
    guids_for_block_indices,
    infer_channels,
    infer_sliders,
    parse_bsg,
    write_mapping,
)
from .manifest import purge_mod_data
from .orchestrator import BesiegeOrchestrator, OrchestratorTimeoutError
from .paths import datacache_dir, mod_data_dir, resolve_besiege_data, resolve_channel_catalog
from .process import find_besiege_pids
from .recorder import is_recording, start_recording, stop_recording
from .run import add_run_parser
from .session import ensure_game, ensure_sandbox, quit_game
from .timeline import build_timeline_from_key_events, write_timeline_json


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--besiege-data",
        default=None,
        help="Path to Besiege_Data. Defaults to the BESIEGE_DATA_PATH environment variable.",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="Seconds to wait for this command to complete.")
    parser.add_argument("--poll-interval", type=float, default=0.25, help="Seconds between protocol-file polls.")


def _orchestrator(args: argparse.Namespace) -> tuple[BesiegeOrchestrator, Path]:
    besiege_data = resolve_besiege_data(args.besiege_data)
    data_dir = mod_data_dir(besiege_data)
    return BesiegeOrchestrator(data_dir, poll_interval=args.poll_interval), besiege_data


def cmd_launch(args: argparse.Namespace) -> int:
    orchestrator, besiege_data = _orchestrator(args)
    session = ensure_game(orchestrator=orchestrator, besiege_data=besiege_data, timeout=args.timeout)
    state = orchestrator.read_state()
    print(
        f"Mod heartbeat detected: scene={state.get('scene')!r} simulating={state.get('simulating')} "
        f"already_running={session.already_running} launched={session.launched}"
    )
    return 0


def cmd_enter_sandbox(args: argparse.Namespace) -> int:
    orchestrator, _ = _orchestrator(args)
    ensure_sandbox(orchestrator=orchestrator, timeout=args.timeout, level=args.level)
    print(f"Entered sandbox: scene={args.level!r}.")
    return 0


def cmd_load_machine(args: argparse.Namespace) -> int:
    orchestrator, _ = _orchestrator(args)
    name = args.machine_name or Path(args.bsg).stem
    installed_name = orchestrator.install_machine(source_bsg=Path(args.bsg), name=f"{name}.bsg")
    sequence = orchestrator.send_command("load_machine", path=installed_name)
    orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
    print(f"load_machine({installed_name!r}) acknowledged (sequence={sequence}).")
    return 0


def cmd_start_sim(args: argparse.Namespace) -> int:
    orchestrator, besiege_data = _orchestrator(args)
    if args.record is not None:
        record_path = Path(args.record) if args.record else datacache_dir() / "recordings" / f"{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        pid = start_recording(
            data_dir=mod_data_dir(besiege_data),
            output=record_path,
            fps=args.record_fps,
            video_bitrate_kbps=args.record_bitrate_kbps,
        )
        print(f"Recording started (ffmpeg pid={pid}) -> {record_path}; stop-sim will finalize it.")
    sequence = orchestrator.send_command("start_sim")
    orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
    state = orchestrator.wait_for_simulating(True, timeout=args.timeout)
    print(f"Simulation started (sequence={sequence}, simulation_state={state.get('simulation_state')!r}).")
    return 0


def cmd_stop_sim(args: argparse.Namespace) -> int:
    orchestrator, besiege_data = _orchestrator(args)
    sequence = orchestrator.send_command("stop_sim")
    orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
    state = orchestrator.wait_for_simulating(False, timeout=args.timeout)
    print(f"Simulation stopped (sequence={sequence}, simulation_state={state.get('simulation_state')!r}).")
    if is_recording(mod_data_dir(besiege_data)):
        video = stop_recording(data_dir=mod_data_dir(besiege_data))
        print(f"Recording saved: {video}")
    return 0


def cmd_dump_channels(args: argparse.Namespace) -> int:
    """Export control_channels.json/csv for a .bsg's runtime block GUIDs.

    Wraps the mod's record_mode.flag protocol end-to-end: ensure the game is
    running, load the machine, run one simulation window until the mod dumps
    channels for the freshly spawned blocks, stop, and copy the exports out.
    The mod only dumps once per record-mode session (channelDumped latches
    true), so any pre-existing exports in the mod data dir are deleted
    first; if they do not reappear within --export-timeout, this raises
    instead of silently returning stale files.
    """
    besiege_data = resolve_besiege_data(args.besiege_data)
    data_dir = mod_data_dir(besiege_data)
    orchestrator = BesiegeOrchestrator(data_dir, poll_interval=args.poll_interval)

    ensure_game(orchestrator=orchestrator, besiege_data=besiege_data, timeout=args.launch_timeout)
    # load_machine requires an active machine, which only exists inside a
    # level: entering the sandbox first is mandatory after a fresh launch.
    ensure_sandbox(orchestrator=orchestrator, timeout=args.timeout, level=args.sandbox_level)

    data_dir.mkdir(parents=True, exist_ok=True)
    channel_json = data_dir / "control_channels.json"
    channel_csv = data_dir / "control_channels.csv"
    for stale in (channel_json, channel_csv):
        stale.unlink(missing_ok=True)

    flag_path = data_dir / "record_mode.flag"
    flag_path.write_text("", encoding="utf-8")

    sim_started = False
    try:
        name = args.machine_name or Path(args.bsg).stem
        installed_name = orchestrator.install_machine(source_bsg=Path(args.bsg), name=f"{name}.bsg")

        sequence = orchestrator.send_command("load_machine", path=installed_name)
        orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
        print(f"load_machine({installed_name!r}) acknowledged.")

        sequence = orchestrator.send_command("start_sim")
        orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
        orchestrator.wait_for_simulating(True, timeout=args.timeout)
        sim_started = True
        print("Simulation started; waiting for control channel export...")

        deadline = time.monotonic() + args.export_timeout
        while time.monotonic() < deadline:
            if channel_json.is_file() and channel_csv.is_file():
                break
            time.sleep(args.poll_interval)
        else:
            raise OrchestratorTimeoutError(
                f"{channel_json.name}/{channel_csv.name} did not appear within {args.export_timeout}s "
                f"at {data_dir} (the mod dumps channels once per record-mode session; if it already "
                "dumped for a previously loaded machine this run, relaunch Besiege and retry)."
            )
        print(f"Exported: {channel_json.name}, {channel_csv.name}")
    finally:
        if sim_started:
            sequence = orchestrator.send_command("stop_sim")
            orchestrator.wait_for_command_result(sequence, timeout=args.timeout)
            orchestrator.wait_for_simulating(False, timeout=args.timeout)
            print("Simulation stopped.")
        flag_path.unlink(missing_ok=True)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in (channel_json, channel_csv):
        dest = out_dir / src.name
        shutil.copy2(src, dest)
        copied.append(dest.name)
    print(f"Copied exports to {out_dir}: {copied}")
    return 0


def cmd_quit(args: argparse.Namespace) -> int:
    orchestrator, _ = _orchestrator(args)
    if not find_besiege_pids():
        print("No running Besiege.exe process found; nothing to quit.")
        return 0
    result = quit_game(orchestrator=orchestrator, timeout=args.timeout, poll_interval=args.poll_interval)
    print(f"Besiege quit result: {result}")
    return 0


def cmd_inspect_machine(args: argparse.Namespace) -> int:
    """Offline authoring helper for MCP-built machines: replay the BSG's
    same-name operation-history JSON into a buildarena.build.Machine and print
    each block's structural caption together with its control channels
    (block guid + channel name + activation address, the exact identifiers
    a timeline JSON or ControllerClient.send_channels uses)."""
    from .inspect_machine import locate_operation_history, machine_inspect_report, rebuild_machine

    # Resolve early: unresolved `..` segments can push deep paths over the
    # Windows MAX_PATH limit and make existing files unreadable.
    bsg_path = Path(args.bsg).resolve()
    json_path = locate_operation_history(bsg_path)
    catalog_path = resolve_channel_catalog(args.catalog)
    _, blocks, _ = parse_bsg(bsg_path, catalog_path=catalog_path)
    channels = infer_channels(blocks, manual_path=args.manual_map, catalog_path=catalog_path)
    sliders = infer_sliders(blocks, catalog_path=catalog_path)
    machine = rebuild_machine(json_path)
    print(f"Machine: {bsg_path}")
    print(f"Build history: {json_path}")
    print(
        machine_inspect_report(
            machine, blocks, channels, sliders=sliders, verbose=args.verbose
        )
    )
    if args.out:
        write_mapping(args.out, channels)
        print(f"Wrote channel map: {args.out}")
    return 0


def cmd_configure_telemetry(args: argparse.Namespace) -> int:
    """Write the ToolKit telemetry-recorder configuration into any .bsg,
    hand-built machines included (telemetry needs no MCP build history).
    Without a target flag this only lists the machine's blocks.
    ``--all-targets`` writes every simulation block (``*``)."""
    bsg_path = Path(args.bsg).resolve()
    target_flags = sum(
        bool(flag) for flag in (args.target_guids, args.target_blocks, args.all_targets)
    )
    if target_flags > 1:
        raise ValueError(
            "configure-telemetry accepts only one of --all-targets, "
            "--target-blocks, or --target-guids."
        )
    if target_flags == 0:
        catalog_path = resolve_channel_catalog(args.catalog)
        _, blocks, _ = parse_bsg(bsg_path, catalog_path=catalog_path)
        print(f"Machine: {bsg_path}")
        print(
            f"Blocks: {len(blocks)}. Nothing written; pass --all-targets "
            "(full machine) or --target-blocks / --target-guids for a subset."
        )
        for block in blocks:
            print(f"{block.local_index:03d} id={block.block_id:<4} {block.name:<28} guid={block.guid or '<missing>'}")
        return 0

    target_guids: tuple[str, ...] = ()
    if args.target_guids:
        target_guids = tuple(args.target_guids)
    if args.target_blocks:
        _, blocks, _ = parse_bsg(bsg_path, catalog_path=None)
        target_guids = tuple(guids_for_block_indices(blocks, args.target_blocks))

    report = configure_telemetry_bsg(
        bsg_path,
        target_guids=target_guids,
        output_basename=args.output_basename or bsg_path.stem,
        output_bsg=args.out,
    )
    print(f"Telemetry configured: {report['written_to']}")
    if report["backup"]:
        print(f"Backup written: {report['backup']}")
    if report.get("targets") == "all":
        print("Targets: all machine blocks (*)")
    else:
        print(f"Targets ({len(report['target_guids'])}): {'; '.join(report['target_guids'])}")
    print(f"Sample rate: {report['sample_rate_hz']} Hz (fixed); output basename: {report['output_basename']}")
    if report["assigned_guids"]:
        print(f"Assigned fresh guids to {len(report['assigned_guids'])} block(s) that had none: {report['assigned_guids']}")
    return 0


def cmd_purge_data(args: argparse.Namespace) -> int:
    """Wipe leftover v3 files, IPC state, and recorder sessions from ModIO data.

    Refuses while Besiege is running. Merged outputs under datacache/ are
    not touched.
    """
    besiege_data = resolve_besiege_data(args.besiege_data)
    pids = find_besiege_pids()
    if pids:
        raise RuntimeError(
            f"Besiege is running (PIDs {pids}); quit the game before purge-data "
            "so leftover files are not locked and a live session is not deleted."
        )
    data_dir = mod_data_dir(besiege_data)
    if not data_dir.is_dir():
        print(f"No ToolKit data dir at {data_dir}; nothing to purge.")
        return 0
    counts = purge_mod_data(data_dir, recordings=not args.keep_recordings)
    print(f"Purged {data_dir}")
    if counts:
        print(", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())))
    else:
        print("Already clean.")
    return 0


def cmd_convert_recording(args: argparse.Namespace) -> int:
    """Auxiliary conversion: turn a recorded_keys.csv (from the mod's
    record_mode.flag session) into a timeline JSON runnable via `run`."""
    catalog_path = resolve_channel_catalog(args.catalog)
    _, blocks, _ = parse_bsg(args.bsg, catalog_path=catalog_path)
    channels = infer_channels(blocks, manual_path=args.manual_map, catalog_path=catalog_path)
    key_events = read_key_events(args.recorded_keys)
    timeline = build_timeline_from_key_events(
        key_events, channels, start_at_seconds=args.start_at_seconds
    )
    write_timeline_json(args.out, timeline)
    print(f"Wrote timeline JSON ({len(timeline['events'])} events, "
          f"duration {timeline['duration_seconds']}s): {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="besiege_cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_run_parser(subparsers, _add_common_args)

    launch_parser = subparsers.add_parser("launch", help="Launch Besiege and wait for the mod heartbeat.")
    _add_common_args(launch_parser)
    launch_parser.set_defaults(func=cmd_launch, timeout=90.0)

    sandbox_parser = subparsers.add_parser(
        "enter-sandbox",
        help="Enter a sandbox level from the title screen (requires mod LoadInTitleScreen).",
    )
    _add_common_args(sandbox_parser)
    sandbox_parser.add_argument(
        "--level",
        default="BARREN EXPANSE",
        help="Level scene name to load (default: the Sandbox scene, 'BARREN EXPANSE').",
    )
    sandbox_parser.set_defaults(func=cmd_enter_sandbox, timeout=90.0)

    load_parser = subparsers.add_parser("load-machine", help="Copy a .bsg into the mod data dir and load it in-game.")
    _add_common_args(load_parser)
    load_parser.add_argument("--bsg", required=True, help="Path to the source .bsg file.")
    load_parser.add_argument("--machine-name", default=None, help="Override the installed file's base name (default: bsg file stem).")
    load_parser.set_defaults(func=cmd_load_machine)

    start_parser = subparsers.add_parser("start-sim", help="Start simulation and wait for it to become active.")
    _add_common_args(start_parser)
    start_parser.add_argument(
        "--record",
        nargs="?",
        const="",
        default=None,
        metavar="MP4_PATH",
        help="Record the Besiege window (1080p H.264) until stop-sim. Optional value = output path (default: <repo>/datacache/recordings/<timestamp>.mp4).",
    )
    start_parser.add_argument("--record-fps", type=int, default=25, help="Capture frame rate (default: 25).")
    start_parser.add_argument("--record-bitrate-kbps", type=int, default=3500, help="Video bitrate in kbps (default: 3500).")
    start_parser.set_defaults(func=cmd_start_sim)

    stop_parser = subparsers.add_parser("stop-sim", help="Stop simulation, wait for it to become inactive, and finalize any active recording.")
    _add_common_args(stop_parser)
    stop_parser.set_defaults(func=cmd_stop_sim)

    dump_parser = subparsers.add_parser(
        "dump-channels",
        help="Load a .bsg, run one simulation window, and export control_channels.json/csv for its runtime block GUIDs.",
    )
    _add_common_args(dump_parser)
    dump_parser.add_argument("--bsg", required=True, help="Path to the source .bsg file.")
    dump_parser.add_argument("--machine-name", default=None, help="Override the installed file's base name (default: bsg file stem).")
    dump_parser.add_argument("--out-dir", required=True, help="Directory to copy control_channels.json/csv into.")
    dump_parser.add_argument("--launch-timeout", type=float, default=90.0, help="Seconds to wait for Besiege to launch if not already running.")
    dump_parser.add_argument("--export-timeout", type=float, default=30.0, help="Seconds to wait for control_channels.json/csv to appear after starting simulation.")
    dump_parser.add_argument("--sandbox-level", default="BARREN EXPANSE", help="Level scene to enter before loading the machine (default: 'BARREN EXPANSE').")
    dump_parser.set_defaults(func=cmd_dump_channels)

    inspect_parser = subparsers.add_parser(
        "inspect-machine",
        help="Show an MCP-built machine's structure and control channels (offline; requires the "
        "same-name .json operation history next to the .bsg).",
    )
    inspect_parser.add_argument("--bsg", required=True, help="Path to the MCP-built .bsg (its <name>.json build history must sit next to it).")
    inspect_parser.add_argument("--catalog", default=None, help="Override the ToolKit-data-dir block_channel_catalog.json path.")
    inspect_parser.add_argument("--manual-map", default=None, help="Optional manual channel override CSV/JSON (e.g. a dump-channels export).")
    inspect_parser.add_argument("--out", default=None, help="Also write the resolved channel map JSON here.")
    inspect_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Also show protocol details: block guids, KeyList positions, and activation implementation.",
    )
    inspect_parser.set_defaults(func=cmd_inspect_machine)

    telemetry_parser = subparsers.add_parser(
        "configure-telemetry",
        help="Write telemetry.* recorder keys + the ToolKit requiredMods entry into any .bsg "
        "(hand-built machines included). Without a target flag, only lists the blocks.",
    )
    telemetry_parser.add_argument("--bsg", required=True, help="Path to the .bsg to configure.")
    telemetry_parser.add_argument(
        "--all-targets",
        action="store_true",
        help="Record every simulation block (writes telemetry.target_guids=*).",
    )
    telemetry_parser.add_argument(
        "--target-guids",
        nargs="*",
        default=(),
        help="Optional GUID subset. Omit all target flags to list blocks.",
    )
    telemetry_parser.add_argument(
        "--target-blocks",
        nargs="*",
        type=int,
        default=(),
        metavar="N",
        help="inspect-machine block indices whose raw x/y/z the ToolKit records at 25 Hz (recommended).",
    )
    telemetry_parser.add_argument(
        "--output-basename",
        default=None,
        help="Base name for the recording files the mod writes (default: the bsg file stem).",
    )
    telemetry_parser.add_argument(
        "--out",
        default=None,
        help="Write the configured copy here instead of configuring in place (in place writes a <name>.bsg.bak backup first).",
    )
    telemetry_parser.add_argument("--catalog", default=None, help="Override the ToolKit-data-dir block_channel_catalog.json path (used only for block names in the listing mode).")
    telemetry_parser.set_defaults(func=cmd_configure_telemetry)

    convert_parser = subparsers.add_parser(
        "convert-recording",
        help="Convert a recorded_keys.csv keyboard session into a timeline JSON runnable via `run` (offline).",
    )
    convert_parser.add_argument("--bsg", required=True, help="Path to the .bsg the keys were recorded against.")
    convert_parser.add_argument("--recorded-keys", required=True, help="Path to the recorded_keys.csv export.")
    convert_parser.add_argument("--catalog", default=None, help="Override the ToolKit-data-dir block_channel_catalog.json path.")
    convert_parser.add_argument("--manual-map", default=None, help="Optional manual channel override CSV/JSON.")
    convert_parser.add_argument(
        "--start-at-seconds",
        type=float,
        default=None,
        help="Shift the timeline so its first event fires at this time (trims recorded idle lead-in).",
    )
    convert_parser.add_argument("--out", required=True, help="Output timeline JSON path.")
    convert_parser.set_defaults(func=cmd_convert_recording)

    purge_parser = subparsers.add_parser(
        "purge-data",
        help="Wipe leftover v3 files, IPC state, and recorder sessions from the ToolKit data dir (game must be closed).",
    )
    _add_common_args(purge_parser)
    purge_parser.add_argument(
        "--keep-recordings",
        action="store_true",
        help="Leave *__manifest.json / *__traj_*.csv / *__keys_*.csv in place.",
    )
    purge_parser.set_defaults(func=cmd_purge_data)

    quit_parser = subparsers.add_parser("quit", help="Quit in-game (Application.Quit); force-kill if it does not exit.")
    _add_common_args(quit_parser)
    quit_parser.set_defaults(func=cmd_quit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
