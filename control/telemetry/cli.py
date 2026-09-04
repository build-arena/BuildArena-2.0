"""uv run python -m telemetry <command> ...

Validate and merge ToolKit telemetry recording sessions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .recording import find_orphan_chunks, list_session_manifests, load_session, write_merged_csvs


def _merge_telemetry(args: argparse.Namespace) -> int:
    if args.manifest:
        manifest_path = Path(args.manifest)
    else:
        manifests = list_session_manifests(args.data_dir, basename=args.basename)
        if not manifests:
            orphans = find_orphan_chunks(args.data_dir)
            detail = (
                f" {len(orphans)} orphan chunk file(s) without a manifest exist "
                "(unfinalized session; the game likely died mid-recording)."
                if orphans
                else ""
            )
            raise SystemExit(f"No telemetry session manifests found in {args.data_dir}.{detail}")
        manifest_path = manifests[-1]
    session = load_session(manifest_path, allow_incomplete=args.allow_incomplete)
    write_merged_csvs(
        session,
        trajectory_csv=args.trajectory_out,
        key_events_csv=args.keys_out,
    )
    print(f"Merged session {session.manifest['session']!r} from {manifest_path}")
    print(f"  trajectory rows: {len(session.trajectory)} -> {args.trajectory_out}")
    if args.keys_out:
        print(f"  key events:      {len(session.key_events)} -> {args.keys_out}")
    if not session.completed or session.error:
        print(f"  WARNING: session incomplete (completed={session.completed}, error={session.error!r})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m telemetry", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser(
        "merge-telemetry",
        help="Strictly merge one ToolKit telemetry recording session (chunked CSVs + manifest) into flat CSVs.",
    )
    merge_group = merge_parser.add_mutually_exclusive_group(required=True)
    merge_group.add_argument("--manifest", help="Path to a specific [session]__manifest.json.")
    merge_group.add_argument("--data-dir", help="ToolKit mod data dir; merges the newest session manifest.")
    merge_parser.add_argument("--basename", help="With --data-dir: only consider sessions of this output basename.")
    merge_parser.add_argument("--trajectory-out", required=True)
    merge_parser.add_argument("--keys-out")
    merge_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Merge a session the mod marked incomplete (aborted/superseded) instead of refusing.",
    )
    merge_parser.set_defaults(func=_merge_telemetry)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
