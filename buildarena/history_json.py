"""Shared valid-only build-history JSON checks for MCP load and rebuild CLI."""

from __future__ import annotations

from pathlib import Path

from .build import Machine
from .paths import resolve_project_path


def rejected_history_kind(*, file_name: str) -> str | None:
    lower_name = file_name.lower()
    if lower_name.endswith(".bsg"):
        return "a .bsg machine file"
    if lower_name == "full.json" or lower_name.endswith("_full.json"):
        return "a *_full.json full-action trace"
    return None


def prepare_history_json(*, history_json: str | Path, machine: Machine) -> Path:
    """Validate a complete path to a valid-only <name>.json and return it resolved.

    Rejects .bsg, full.json, and *_full.json. Requires a non-empty operation
    list that begins with start. Does not rebuild; callers then use
    Machine.from_file.
    """
    raw = history_json if isinstance(history_json, str) else str(history_json)
    if raw != raw.strip() or raw.strip() == "":
        raise ValueError(
            "history_json must be a complete path to a valid-only <name>.json file, "
            "without surrounding whitespace."
        )

    given = Path(raw)
    rejected = rejected_history_kind(file_name=given.name)
    if rejected is not None:
        raise ValueError(
            f"history_json must be a valid-only <name>.json build history, not {rejected}."
        )

    resolved = given if given.is_absolute() else resolve_project_path(path=given)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Build-history JSON not found: {resolved}. "
            "Pass the complete path to a valid-only <name>.json file."
        )
    if resolved.suffix.lower() != ".json":
        raise ValueError(
            f"{resolved} is not a JSON build history. Pass the complete path to a <name>.json file."
        )

    record = machine.load_operation_history(resolved)
    if not isinstance(record, list) or len(record) == 0:
        raise ValueError(
            f"{resolved} is not a valid-only build history: expected a non-empty JSON "
            "list of operations."
        )
    first = record[0]
    first_op = first.get("op") if isinstance(first, dict) else None
    if first_op != "start":
        raise ValueError(
            f"{resolved} is not a complete machine build history: it begins with "
            f"{first_op!r} instead of 'start'."
        )
    return resolved.resolve()
