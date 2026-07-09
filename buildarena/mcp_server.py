import inspect
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import sys
from collections.abc import Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server import FastMCP

from buildarena.build import Machine
from buildarena.paths import get_saved_machine_dir

_state: dict[str, Machine] = {}
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
        if machine is not None:
            _save_machine_to_file(machine=machine)


mcp = FastMCP(name="buildarena-specimen", lifespan=server_lifespan)


def _get_specimen() -> Machine:
    machine = _state.get("specimen")
    if machine is None:
        raise RuntimeError("Machine lifespan is not active; call create_machine_lifespan first.")
    return machine


def _save_machine_to_file(*, machine: Machine) -> None:
    if not machine.started:
        raise RuntimeError("Cannot save machine before start() has created the Starting Block.")
    machine.to_file(output_dir=machine.save_dir)


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
    do_collision: bool = True,
    collision_tolerance: float = 0.01,
    write_full_history: bool = True,
) -> str:
    """Create a new active machine lifespan using machine_name plus a short timestamp."""
    if _state.get("specimen") is not None:
        raise RuntimeError("Machine lifespan is already active; close_machine_lifespan first.")

    name = _machine_name_with_timestamp(machine_name=machine_name)
    machine_save_dir = get_saved_machine_dir() / name
    if machine_save_dir.exists():
        raise FileExistsError(f"Machine save directory already exists: {machine_save_dir}")
    _state["specimen"] = Machine(
        name=name,
        save_dir=str(machine_save_dir),
        note=note,
        do_collision=do_collision,
        collision_tolerance=collision_tolerance,
        write_full_history=write_full_history,
    )
    return f"Created machine lifespan '{name}' at {machine_save_dir}"


def close_machine_lifespan() -> str:
    """Save the active machine to files and close its lifespan."""
    machine = _get_specimen()
    _save_machine_to_file(machine=machine)
    del _state["specimen"]
    return f"Saved and closed machine lifespan '{machine.name}' at {machine.save_dir}"


def save_machine() -> str:
    """Save the current specimen to a .bsg file and operation-history JSON."""
    machine = _get_specimen()
    _save_machine_to_file(machine=machine)
    return f"Saved machine '{machine.name}' to {machine.save_dir}"


def _iter_machine_operation_groups() -> dict[str, list[Callable]]:
    groups: dict[str, list[Callable]] = {}
    for attr_name in dir(Machine):
        attr = getattr(Machine, attr_name)
        if callable(attr) and getattr(attr, "_is_operation", False):
            group_name = attr._group
            groups.setdefault(group_name, []).append(attr)
    return groups


def _gather_machine_operations():
    """Return a deduplicated list of Machine operations preserving group order."""
    seen: set[str] = set()
    ordered_ops: list = []
    for funcs in _iter_machine_operation_groups().values():
        for fn in funcs:
            name = fn.__name__
            if name in seen:
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


def _register_lifecycle_tools() -> None:
    for fn in (create_machine_lifespan, close_machine_lifespan, save_machine):
        _register_tool(fn=fn)


def _register_machine_operation_tools() -> None:
    """Bulk-register Machine operation proxies onto the FastMCP server."""
    for fn in _gather_machine_operations():
        proxy = _machine_operation_proxy(operation_name=fn.__name__, operation_fn=fn)
        _register_tool(fn=proxy)


async def main():
    _register_lifecycle_tools()
    _register_machine_operation_tools()
    await mcp.run_stdio_async()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
