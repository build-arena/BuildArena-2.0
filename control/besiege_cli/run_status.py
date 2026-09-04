"""Structured run_status.json writer."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from controller_sdk.protocol import RUN_STATUS_SCHEMA, atomic_write_json


class RunStatus:
    def __init__(self, path: Path, *, run_id: str) -> None:
        self.path = path
        self.payload: dict[str, Any] = {
            "schema": RUN_STATUS_SCHEMA,
            "run_id": run_id,
            "phase": "created",
            "result": "running",
            "updated_at": time.time(),
            "error": "",
            "cleanup_errors": [],
            "critical_errors": [],
            "simulation_time": None,
            "last_telemetry_sequence": None,
            "last_action_ack": None,
            "controller_pid": None,
            "controller_status": "",
            "screen_recorder_status": "",
            "telemetry_recorder_status": "",
        }
        self.flush()

    def set_phase(self, phase: str, **fields: Any) -> None:
        """Write a phase change immediately."""
        self.update(phase=phase, **fields)

    def update(self, **fields: Any) -> None:
        self.payload.update(fields)
        self.payload["updated_at"] = time.time()
        self.flush()

    def refresh(self, **fields: Any) -> None:
        """Low-frequency snapshot update; does not change phase by itself."""
        self.update(**fields)

    def append_cleanup_error(self, message: str) -> None:
        errors = list(self.payload.get("cleanup_errors") or [])
        errors.append(message)
        self.payload["cleanup_errors"] = errors
        self.payload["updated_at"] = time.time()
        self.flush()

    def append_critical_error(self, message: str) -> None:
        errors = list(self.payload.get("critical_errors") or [])
        errors.append(message)
        self.payload["critical_errors"] = errors
        self.payload["error"] = message
        self.payload["result"] = "failed"
        self.payload["updated_at"] = time.time()
        self.flush()

    def flush(self) -> None:
        atomic_write_json(self.path, self.payload, durable=False)
