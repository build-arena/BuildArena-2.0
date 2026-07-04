"""
Rebuild a machine `.bsg` from one machine operation-history JSON record.

Use this when a machine structure was accidentally edited in-game and you want
to recover a clean BuildArena-valid machine from operating history.

Usage:
    uv run python scripts/rebuild_from_record.py --record-json ".local/Machine/<machine>/<machine>.json"

Optional arguments:
    --base-name "<name>"                 Base name for rebuilt output.

Notes:
    - `--record-json` must point to a valid-only operating history JSON.
    - Do not pass `*_full.json` or `full.json`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buildarena.build import Machine
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


def _load_json_record(*, record_path: Path) -> Any:
    if not record_path.is_file():
        raise FileNotFoundError(f"Record JSON not found: {record_path}")

    lower_name = record_path.name.lower()
    if lower_name == "full.json" or lower_name.endswith("_full.json"):
        raise ValueError(
            "Input must be a valid-only operating history JSON, not a full history JSON."
        )

    with open(file=record_path, mode="r", encoding="utf-8") as file:
        return json.load(fp=file)


def _extract_operation_history(*, record: Any) -> list[dict[str, Any]]:
    if isinstance(record, list):
        operation_history = record
    elif isinstance(record, dict):
        operation_history = record.get("operation_history")
        if operation_history is None:
            operation_history = record.get("operations")
    else:
        raise ValueError("Record JSON must be a list or an object containing operation_history.")

    if not isinstance(operation_history, list):
        raise ValueError("Operation history must be a list.")

    normalized_history: list[dict[str, Any]] = []
    for index, operation in enumerate(operation_history):
        if not isinstance(operation, dict):
            raise ValueError(f"Operation at index {index} must be an object.")
        operation_name = operation.get("op")
        if not isinstance(operation_name, str) or operation_name.strip() == "":
            raise ValueError(f"Operation at index {index} must define a non-empty string op.")
        operation_params = operation.get("params")
        if not isinstance(operation_params, dict):
            raise ValueError(f"Operation at index {index} must define object params.")
        normalized_history.append(operation)

    if len(normalized_history) == 0:
        raise ValueError("Operation history cannot be empty.")

    return normalized_history


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


def _validate_operations_exist(
    *,
    machine: Machine,
    operation_history: list[dict[str, Any]],
) -> None:
    for index, operation in enumerate(operation_history):
        operation_name = operation["op"]
        if operation_name not in machine.operations:
            raise ValueError(f"Operation at index {index} is not registered: {operation_name}")


def rebuild_machine_from_record(
    *,
    record_path: Path,
    base_name: str | None = None,
) -> RebuildResult:
    resolved_record_path = resolve_project_path(path=record_path)
    record = _load_json_record(record_path=resolved_record_path)
    operation_history = _extract_operation_history(record=record)

    raw_base_name = resolved_record_path.stem if base_name is None else base_name
    machine_name = _machine_name_for_rebuild(base_name=raw_base_name)
    output_dir = get_saved_machine_dir() / machine_name
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing machine directory: {output_dir}")

    machine = Machine(
        name=machine_name,
        save_dir=str(output_dir),
        write_full_history=False,
    )
    _validate_operations_exist(machine=machine, operation_history=operation_history)
    machine.rebuild_from_history(operation_history=operation_history)
    machine.to_file(output_dir=output_dir)

    return RebuildResult(
        machine_name=machine_name,
        output_dir=output_dir,
        bsg_path=output_dir / f"{machine_name}.bsg",
        operation_history_path=output_dir / f"{machine_name}.json",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild a BuildArena machine from one operation-history JSON record."
    )
    parser.add_argument(
        "--record-json",
        required=True,
        type=Path,
        help="Path to the single machine operation-history JSON. Do not pass *_full.json.",
    )
    parser.add_argument(
        "--base-name",
        default=None,
        help="Optional base machine name. Defaults to the input JSON stem.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    result = rebuild_machine_from_record(
        record_path=args.record_json,
        base_name=args.base_name
    )
    print(f"machine_name={result.machine_name}")
    print(f"output_dir={result.output_dir}")
    print(f"bsg_path={result.bsg_path}")
    print(f"operation_history_path={result.operation_history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
