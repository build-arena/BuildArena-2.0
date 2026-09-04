"""
Rebuild a machine `.bsg` from one machine operation-history JSON record.

Reuses the same prepare + Machine.from_file path as MCP
`load_machine_from_history`, then writes a new machine with `to_file`.

Usage:
    uv run python scripts/rebuild_from_record.py --record-json ".local/Machine/<machine>/<machine>.json"

    uv run python scripts/rebuild_from_record.py `
      --record-json control/examples/Reusable_Heavy_Launcher.json `
      --machine-name Reusable_Heavy_Launcher `
      --replace

Optional arguments:
    --base-name "<name>"       Base name for a timestamped rebuilt output.
    --machine-name "<name>"    Fixed output machine name (no timestamp).
    --replace                  Delete the output directory if it already exists.

Notes:
    - `--record-json` must be a complete path to a valid-only <name>.json.
    - Do not pass `*_full.json`, `full.json`, or a `.bsg`.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buildarena.build import Machine
from buildarena.history_json import prepare_history_json
from buildarena.paths import get_saved_machine_dir, resolve_project_path


_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_MACHINE_BASE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,40}$")


@dataclass(frozen=True, kw_only=True)
class RebuildResult:
    machine_name: str
    output_dir: Path
    bsg_path: Path
    operation_history_path: Path


def _validate_base_name(*, base_name: str) -> str:
    if base_name != base_name.strip():
        raise ValueError("base_name must not start or end with whitespace.")
    if base_name in {".", ".."}:
        raise ValueError("base_name must not be '.' or '..'.")
    if not _MACHINE_BASE_NAME_PATTERN.fullmatch(base_name):
        raise ValueError(
            "base_name must be 1-41 characters using only letters, numbers, spaces, '_', '-', or '.'."
        )
    stem = base_name.split(".", maxsplit=1)[0].upper()
    if stem in _RESERVED_WINDOWS_NAMES:
        raise ValueError(f"base_name uses a reserved Windows device name: {base_name}")
    return base_name


def _machine_name_for_rebuild(*, base_name: str) -> str:
    validated_base_name = _validate_base_name(base_name=base_name)
    timestamp = datetime.now(tz=None).strftime("%y%m%d_%H%M%S_%f")
    return f"{validated_base_name}_rebuilt_{timestamp}"


def rebuild_machine_from_record(
    *,
    record_path: Path,
    base_name: str | None = None,
    machine_name: str | None = None,
    replace: bool = False,
) -> RebuildResult:
    if machine_name is not None and base_name is not None:
        raise ValueError("Pass only one of machine_name or base_name.")

    resolved_record_path = resolve_project_path(path=record_path)
    if machine_name is not None:
        resolved_machine_name = _validate_base_name(base_name=machine_name)
    else:
        raw_base_name = resolved_record_path.stem if base_name is None else base_name
        resolved_machine_name = _machine_name_for_rebuild(base_name=raw_base_name)
    output_dir = get_saved_machine_dir() / resolved_machine_name
    if output_dir.exists():
        if not replace:
            raise FileExistsError(f"Refusing to overwrite existing machine directory: {output_dir}")
        shutil.rmtree(output_dir)

    machine = Machine(
        name=resolved_machine_name,
        save_dir=str(output_dir),
        write_full_history=False,
    )
    history_path = prepare_history_json(history_json=resolved_record_path, machine=machine)
    machine.from_file(history_path)
    machine.to_file(output_dir=output_dir)

    return RebuildResult(
        machine_name=resolved_machine_name,
        output_dir=output_dir,
        bsg_path=output_dir / f"{resolved_machine_name}.bsg",
        operation_history_path=output_dir / f"{resolved_machine_name}.json",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild a BuildArena machine from one operation-history JSON record."
    )
    parser.add_argument(
        "--record-json",
        required=True,
        type=Path,
        help="Complete path to a valid-only <name>.json. Do not pass .bsg or *_full.json.",
    )
    parser.add_argument(
        "--base-name",
        default=None,
        help="Optional base machine name for a timestamped rebuilt output. Defaults to the input JSON stem.",
    )
    parser.add_argument(
        "--machine-name",
        default=None,
        help="Fixed output machine name without a timestamp. Use --replace to overwrite.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete the output machine directory if it already exists.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    result = rebuild_machine_from_record(
        record_path=args.record_json,
        base_name=args.base_name,
        machine_name=args.machine_name,
        replace=args.replace,
    )
    print(f"machine_name={result.machine_name}")
    print(f"output_dir={result.output_dir}")
    print(f"bsg_path={result.bsg_path}")
    print(f"operation_history_path={result.operation_history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
