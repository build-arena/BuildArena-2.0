"""Telemetry profile expansion for protocol v4.

``full`` is the default for live, bulk, and the offline recorder.
``profile`` and explicit field lists are mutually exclusive.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

TARGET_FIELD_BITS: dict[str, int] = {
    "position": 1,
    "rotation": 2,
    "velocity": 4,
    "angular_velocity": 8,
    "fuel": 16,
    "fire": 32,
    "steam": 64,
    "ice": 128,
    "health": 256,
    "buoyancy": 512,
}
MACHINE_FIELD_BITS: dict[str, int] = {
    "machine_integrity": 2,
    "alive_block_count": 4,
}

PROFILE_FULL = "full"
PROFILE_POSITION_ONLY = "position-only"
KNOWN_PROFILES = (PROFILE_FULL, PROFILE_POSITION_ONLY)

FULL_TARGET_FIELDS = (
    "position",
    "rotation",
    "velocity",
    "angular_velocity",
    "fuel",
    "fire",
    "steam",
    "ice",
    "health",
    "buoyancy",
)
FULL_MACHINE_FIELDS = (
    "machine_integrity",
    "alive_block_count",
)
POSITION_ONLY_TARGET_FIELDS = ("position",)
POSITION_ONLY_MACHINE_FIELDS: tuple[str, ...] = ()


class TelemetryProfileError(ValueError):
    """A telemetry profile or custom field selection is invalid."""


def mask_for_fields(fields: Iterable[str], table: Mapping[str, int], *, label: str) -> tuple[int, tuple[str, ...]]:
    selected: list[str] = []
    seen: set[str] = set()
    mask = 0
    for raw in fields:
        name = str(raw)
        if name in seen:
            raise TelemetryProfileError(f"Duplicate {label} field {name!r}.")
        if name not in table:
            known = ", ".join(table)
            raise TelemetryProfileError(
                f"Unknown {label} field {name!r}; known fields: {known}."
            )
        seen.add(name)
        selected.append(name)
        mask |= table[name]
    return mask, tuple(selected)


def expand_profile(profile: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if profile == PROFILE_FULL:
        return FULL_TARGET_FIELDS, FULL_MACHINE_FIELDS
    if profile == PROFILE_POSITION_ONLY:
        return POSITION_ONLY_TARGET_FIELDS, POSITION_ONLY_MACHINE_FIELDS
    raise TelemetryProfileError(
        f"Unknown telemetry profile {profile!r}; expected {PROFILE_FULL!r} or "
        f"{PROFILE_POSITION_ONLY!r}."
    )


def resolve_telemetry_selection(
    *,
    profile: str | None = None,
    target_fields: Iterable[str] | None = None,
    machine_fields: Iterable[str] | None = None,
    default_profile: str = PROFILE_FULL,
) -> dict[str, Any]:
    """Return the expanded field lists and masks.

    Rules:
    - profile XOR explicit fields; both present is a hard error.
    - neither present uses ``default_profile`` (``full``).
    - empty target + empty machine is a hard error.
    """
    explicit = target_fields is not None or machine_fields is not None
    if profile is not None and explicit:
        raise TelemetryProfileError(
            "Provide either profile or target_fields/machine_fields, not both."
        )
    resolved_profile = profile
    if not explicit:
        resolved_profile = default_profile if profile is None else profile
        targets, machines = expand_profile(resolved_profile)
    else:
        targets_list = () if target_fields is None else tuple(target_fields)
        machines_list = () if machine_fields is None else tuple(machine_fields)
        target_mask, targets = mask_for_fields(
            targets_list, TARGET_FIELD_BITS, label="target"
        )
        machine_mask, machines = mask_for_fields(
            machines_list, MACHINE_FIELD_BITS, label="machine"
        )
        if target_mask == 0 and machine_mask == 0:
            raise TelemetryProfileError(
                "Custom telemetry selection must include at least one target or machine field."
            )
        return {
            "profile": None,
            "target_fields": targets,
            "machine_fields": machines,
            "target_field_mask": target_mask,
            "machine_field_mask": machine_mask,
        }

    target_mask, targets = mask_for_fields(
        targets, TARGET_FIELD_BITS, label="target"
    )
    machine_mask, machines = mask_for_fields(
        machines, MACHINE_FIELD_BITS, label="machine"
    )
    return {
        "profile": resolved_profile,
        "target_fields": targets,
        "machine_fields": machines,
        "target_field_mask": target_mask,
        "machine_field_mask": machine_mask,
    }
