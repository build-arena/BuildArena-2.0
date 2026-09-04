"""Unified Besiege controller runner.

`besiege_cli run` takes a machine (.bsg), a controller (.py policy or .json
timeline), and a sandbox level, and drives Besiege end-to-end: prepares a
temporary run-bound BSG (the original is never modified), launches the game,
enters the sandbox, loads the machine, executes the controller, and cleans
up. Communication goes through BuildArena ToolKit's file-based protocol.
Every wait has an explicit timeout and raises instead of silently giving up.
"""

from .cli import main
from .machine import ControlChannel, infer_channels, parse_bsg, prepare_machine_bsg
from .orchestrator import BesiegeOrchestrator, OrchestratorCommandError, OrchestratorTimeoutError

__all__ = [
    "BesiegeOrchestrator",
    "ControlChannel",
    "OrchestratorCommandError",
    "OrchestratorTimeoutError",
    "infer_channels",
    "main",
    "parse_bsg",
    "prepare_machine_bsg",
]
