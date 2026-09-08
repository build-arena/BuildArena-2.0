import argparse
import inspect
import re
import tomllib
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import sys
from collections.abc import Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server import FastMCP

from buildarena.build import Machine
from buildarena.history_json import prepare_history_json
from buildarena.paths import get_saved_machine_dir

_state: dict[str, Machine] = {}
DEFAULT_TOOLS_CONFIG_PATH = Path(__file__).with_name("mcp_tools.toml")
_tool_config: dict = {}
_save_root: Path | None = None
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_MACHINE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")


@asynccontextmanager
async def server_lifespan(_server):
    """Ensure an active machine state is persisted when the MCP server shuts down."""
    try:
        yield {}
    finally:
        machine = _state.get("specimen")
        if machine is None:
            return
        _save_machine_to_file(machine=machine)


mcp = FastMCP(name="buildarena-specimen", lifespan=server_lifespan)


def _get_specimen() -> Machine:
    machine = _state.get("specimen")
    if machine is None:
        raise RuntimeError("Machine lifespan is not active; call create_machine_lifespan first.")
    return machine


def _save_machine_to_file(*, machine: Machine, spawn_y: float | None = None) -> None:
    if not machine.started:
        raise RuntimeError("Cannot save machine before start() has created the Starting Block.")
    machine.to_file(output_dir=machine.save_dir, spawn_y=spawn_y)


def _validate_machine_name(*, machine_name: str) -> str:
    if machine_name != machine_name.strip():
        raise ValueError("machine_name must not start or end with whitespace.")
    if machine_name in {".", ".."}:
        raise ValueError("machine_name must not be '.' or '..'.")
    if not _MACHINE_NAME_PATTERN.fullmatch(machine_name):
        raise ValueError(
            "machine_name must be 1-64 characters using only letters, numbers, spaces, '_', '-', or '.'."
        )
    stem = machine_name.split(".", maxsplit=1)[0].upper()
    if stem in _RESERVED_WINDOWS_NAMES:
        raise ValueError(f"machine_name uses a reserved Windows device name: {machine_name}")
    return machine_name


def _machine_name_with_timestamp(*, machine_name: str) -> str:
    validated_name = _validate_machine_name(machine_name=machine_name)
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    return f"{validated_name}_{timestamp}"


def create_machine_lifespan(
    machine_name: str,
    note: str | None = None,
) -> str:
    """Create a new active machine lifespan using machine_name plus a short timestamp.

    Args:
        machine_name: Base name; a short timestamp is appended.
        note: Optional description of the machine.

    Returns:
        str: Created lifespan name and save directory.

    Example: create_machine_lifespan(machine_name="demo").
    After creating, either call start() to build from scratch, or
    load_machine_from_history to reconstruct a previous valid-only
    <name>.json build history and continue editing that machine.
    """
    if _state.get("specimen") is not None:
        raise RuntimeError("Machine lifespan is already active; close_machine_lifespan first.")

    name = _machine_name_with_timestamp(machine_name=machine_name)
    save_root = _save_root if _save_root is not None else get_saved_machine_dir()
    machine_save_dir = save_root / name
    if machine_save_dir.exists():
        raise FileExistsError(f"Machine save directory already exists: {machine_save_dir}")
    _state["specimen"] = Machine(
        name=name,
        save_dir=str(machine_save_dir),
        note=note,
    )
    return f"Created machine lifespan '{name}' at {machine_save_dir}"


def close_machine_lifespan(spawn_y: float | None = None) -> str:
    """Save the active machine to files and close its lifespan.

    Args:
        spawn_y: Export spawn height (game vertical axis, game length units). When omitted
            (the default), it is inferred from the machine's collision
            geometry so it lands on the ground instead of falling/bouncing
            from a fixed height.

    Returns:
        str: Saved machine name and output directory.

    Limits: saves authoring state; does not inspect runtime telemetry.
    Example: call with no arguments to infer spawn height.
    """
    machine = _get_specimen()
    _save_machine_to_file(machine=machine, spawn_y=spawn_y)
    del _state["specimen"]
    return f"Saved and closed machine lifespan '{machine.name}' at {machine.save_dir}"


def load_machine_from_history(history_json: str) -> str:
    """Replay a valid-only build-history JSON into the current new lifespan.

    Call this after create_machine_lifespan. The reconstructed machine lives
    only on that new lifespan: the source JSON is read, never overwritten.
    Replay re-executes the original operations (start, attach_block_to, ...),
    so the new machine's history is a copy of those operations — not a single
    load record that would break if the source file later moves.

    Args:
        history_json: Complete path to a valid-only <name>.json written by
            save_machine or close_machine_lifespan. Absolute paths or
            repository-relative paths are accepted; any directory is fine.
            Do not pass a .bsg, *_full.json, or full.json. Do not pass a
            machine name or folder.

    Returns:
        str: Rebuild status plus the reconstructed machine summary.

    Example: load_machine_from_history(history_json="saved/demo.json").
    """
    machine = _get_specimen()
    resolved = prepare_history_json(history_json=history_json, machine=machine)
    machine.from_file(resolved)
    machine.update_prompt(
        pre_msg=(
            f"Rebuilt machine '{machine.name}' from {resolved} "
            f"({len(machine.blocks)} blocks, {len(machine.operation_history)} operations)."
        ),
        complete=True,
        return_summary=True,
    )
    return machine.prompt


def save_machine(spawn_y: float | None = None) -> str:
    """Save the authoritative build history and its .bsg export.

    Args:
        spawn_y: Export spawn height (game vertical axis, game length units). When omitted
            (the default), it is inferred from the machine's collision
            geometry so it lands on the ground instead of falling/bouncing
            from a fixed height.

    Returns:
        str: Saved machine name and output directory.

    Limits: saves authoring state; does not inspect runtime telemetry.
    Example: call with no arguments to infer spawn height.
    """
    machine = _get_specimen()
    _save_machine_to_file(machine=machine, spawn_y=spawn_y)
    return f"Saved machine '{machine.name}' to {machine.save_dir}"


def _iter_machine_operation_groups() -> dict[str, list[Callable]]:
    groups: dict[str, list[Callable]] = {}
    for attr_name in dir(Machine):
        attr = getattr(Machine, attr_name)
        if callable(attr) and getattr(attr, "_is_operation", False):
            group_name = attr._group
            groups.setdefault(group_name, []).append(attr)
    return groups


def _load_tool_config(config_path: Path) -> dict:
    if not config_path.is_file():
        raise FileNotFoundError(f"MCP tools config not found: {config_path}")
    with config_path.open("rb") as config_file:
        return tomllib.load(config_file)


def _group_enabled(*, config: dict, group_name: str) -> bool:
    group_config = config.get("tool_groups", {})
    if group_name not in group_config:
        raise KeyError(f"MCP tool group '{group_name}' is missing from tool_groups config.")
    return bool(group_config[group_name])


def _tool_enabled(*, config: dict, tool_name: str) -> bool:
    return bool(config.get("tools", {}).get(tool_name, True))


def _gather_machine_operations(*, config: dict):
    """Return a deduplicated list of Machine operations preserving group order."""
    seen: set[str] = set()
    ordered_ops: list = []
    for group_name, funcs in _iter_machine_operation_groups().items():
        if not _group_enabled(config=config, group_name=group_name):
            continue
        for fn in funcs:
            name = fn.__name__
            if name in seen:
                continue
            if not _tool_enabled(config=config, tool_name=name):
                continue
            ordered_ops.append(fn)
            seen.add(name)
    return ordered_ops


def _machine_operation_proxy(*, operation_name: str, operation_fn: Callable) -> Callable:
    signature = inspect.signature(operation_fn)
    parameters = [
        parameter
        for name, parameter in signature.parameters.items()
        if name != "self"
    ]
    proxy_signature = inspect.Signature(
        parameters=parameters,
        return_annotation=signature.return_annotation,
    )

    def proxy(**kwargs):
        machine = _get_specimen()
        operation = getattr(machine, operation_name)
        return operation(**kwargs)

    proxy.__name__ = operation_name
    proxy.__doc__ = operation_fn.__doc__
    proxy.__signature__ = proxy_signature
    return proxy


def _register_tool(*, fn) -> None:
    description = (fn.__doc__ or "").strip() or f"Machine operation '{fn.__name__}'"
    mcp.add_tool(fn=fn, description=description)


def _register_lifecycle_tools(*, config: dict) -> None:
    for fn in (
        create_machine_lifespan,
        close_machine_lifespan,
        load_machine_from_history,
    ):
        _register_tool(fn=fn)
    if _group_enabled(config=config, group_name="save") and _tool_enabled(
        config=config, tool_name="save_machine"
    ):
        _register_tool(fn=save_machine)


def _register_machine_operation_tools(*, config: dict) -> None:
    """Bulk-register Machine operation proxies onto the FastMCP server."""
    for fn in _gather_machine_operations(config=config):
        proxy = _machine_operation_proxy(operation_name=fn.__name__, operation_fn=fn)
        _register_tool(fn=proxy)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start BuildArena MCP server.")
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=None,
        help="Override SAVED_MACHINE_DIR for machine lifespan output.",
    )
    parser.add_argument(
        "--tools-config",
        type=Path,
        default=DEFAULT_TOOLS_CONFIG_PATH,
        help="TOML file controlling which MCP tool groups and tools are registered.",
    )
    return parser.parse_args()


async def main():
    global _tool_config, _save_root
    args = _parse_args()
    _tool_config = _load_tool_config(args.tools_config)
    _save_root = args.save_dir.resolve() if args.save_dir is not None else get_saved_machine_dir()
    _register_lifecycle_tools(config=_tool_config)
    _register_machine_operation_tools(config=_tool_config)
    await mcp.run_stdio_async()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
