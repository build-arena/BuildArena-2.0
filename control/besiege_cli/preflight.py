"""Run-start rejection of v3/legacy assets. No migration is performed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from controller_sdk.protocol import (
    LEGACY_TRACKER_KEY_PREFIX,
    REJECTED_MOD_IDS,
    REJECTED_MOD_NAMES,
    REJECTED_SCHEMAS,
    REJECTED_TOOLKIT_VERSIONS,
    TOOLKIT_MOD_ID,
    TOOLKIT_MOD_VERSION,
)
from controller_sdk.profiles import resolve_telemetry_selection

from .machine import parse_bsg


class PreflightError(ValueError):
    """A source asset is not acceptable for protocol v4."""


def reject_legacy_bsg(source_bsg: str | Path) -> None:
    tree, _blocks, _ = parse_bsg(source_bsg, catalog_path=None)
    root = tree.getroot()
    data = None
    for child in root:
        if child.tag in ("Data", "MachineData"):
            data = child
            break
    if data is None:
        return
    for child in data:
        key = child.attrib.get("key", "")
        if key.startswith(LEGACY_TRACKER_KEY_PREFIX):
            raise PreflightError(
                f"{source_bsg} still carries retired {key!r}. v4 does not migrate "
                "tracker.* keys; rewrite the machine to telemetry.* configuration."
            )
        if key == "requiredMods" and child.text:
            for entry in child.text.splitlines():
                _reject_required_mod(entry.strip(), source_bsg)


def _reject_required_mod(entry: str, source_bsg: str | Path) -> None:
    if not entry:
        return
    for rejected in REJECTED_MOD_IDS:
        if rejected in entry:
            raise PreflightError(
                f"{source_bsg} requiredMods names retired mod {rejected}. "
                "v4 does not migrate old mods."
            )
    for name in REJECTED_MOD_NAMES:
        if name in entry:
            raise PreflightError(
                f"{source_bsg} requiredMods names retired mod {name!r}."
            )
    if TOOLKIT_MOD_ID in entry:
        for version in REJECTED_TOOLKIT_VERSIONS:
            if f"~L~{version}~" in entry:
                raise PreflightError(
                    f"{source_bsg} declares ToolKit {version}, which is retired. "
                    f"Current requirement is {TOOLKIT_MOD_VERSION}."
                )


def reject_legacy_schema(payload: dict[str, Any], *, label: str) -> None:
    schema = payload.get("schema")
    if schema in REJECTED_SCHEMAS:
        raise PreflightError(
            f"{label} uses retired schema {schema!r}. Rewrite it for protocol v4."
        )


def expand_run_telemetry(args: Any) -> dict[str, Any]:
    profile = getattr(args, "telemetry_profile", None)
    target_fields = getattr(args, "telemetry_fields", None)
    machine_fields = getattr(args, "machine_fields", None)
    if profile in ("full", "position-only") and (
        target_fields is not None or machine_fields is not None
    ):
        raise PreflightError(
            "Provide either --telemetry-profile or custom --telemetry-fields/"
            "--machine-fields, not both."
        )
    if profile == "custom":
        profile = None
    return resolve_telemetry_selection(
        profile=profile,
        target_fields=target_fields,
        machine_fields=machine_fields,
    )
