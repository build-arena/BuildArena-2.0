"""Strict BAT4/BTM4 telemetry codec with a machine-field frame section."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .protocol import PROTOCOL_VERSION, TELEMETRY_MAGIC, TELEMETRY_MARKER_MAGIC
from .profiles import MACHINE_FIELD_BITS, TARGET_FIELD_BITS

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - broken installation only
    raise ImportError(
        "controller_sdk BAT4 telemetry support requires NumPy; run `uv sync`. "
        "No non-NumPy fallback is provided."
    ) from exc

MAGIC = TELEMETRY_MAGIC
VERSION = PROTOCOL_VERSION
FIELD_POSITION = TARGET_FIELD_BITS["position"]
FIELD_ROTATION = TARGET_FIELD_BITS["rotation"]
FIELD_VELOCITY = TARGET_FIELD_BITS["velocity"]
FIELD_ANGULAR_VELOCITY = TARGET_FIELD_BITS["angular_velocity"]
FIELD_FUEL = TARGET_FIELD_BITS["fuel"]
FIELD_FIRE = TARGET_FIELD_BITS["fire"]
FIELD_STEAM = TARGET_FIELD_BITS["steam"]
FIELD_ICE = TARGET_FIELD_BITS["ice"]
FIELD_HEALTH = TARGET_FIELD_BITS["health"]
FIELD_BUOYANCY = TARGET_FIELD_BITS["buoyancy"]
KNOWN_FIELD_MASK = sum(TARGET_FIELD_BITS.values())
FLAG_RECORD_NAMES = frozenset(
    {
        "fuel_valid",
        "fire_burning",
        "fire_burned",
        "fire_valid",
        "steam",
        "steam_valid",
        "ice",
        "ice_valid",
        "health_valid",
        "buoyancy_valid",
    }
)
MACHINE_INTEGRITY = MACHINE_FIELD_BITS["machine_integrity"]
MACHINE_ALIVE_BLOCK_COUNT = MACHINE_FIELD_BITS["alive_block_count"]
KNOWN_MACHINE_MASK = MACHINE_INTEGRITY | MACHINE_ALIVE_BLOCK_COUNT

# magic, version, header_size, sequence, episode, applied, sim_time,
# count, target_mask, record_size, slot, simulating, machine_mask,
# machine_section_size, reserved0, reserved1
HEADER = struct.Struct("<4sHHqiifHHHBBHHii")
HEADER_SIZE = 48
PACKED_HEADER_SIZE = HEADER.size

MARKER = struct.Struct("<4sHHqBBHi")
MARKER_MAGIC = TELEMETRY_MARKER_MAGIC
MARKER_SIZE = 24
PACKED_MARKER_SIZE = MARKER.size


class TelemetryCodecError(ValueError):
    """A BAT4/BTM4 payload is malformed or incompatible."""


def machine_section_size(machine_field_mask: int) -> int:
    if isinstance(machine_field_mask, bool) or not isinstance(machine_field_mask, int):
        raise TypeError("machine_field_mask must be an integer.")
    if machine_field_mask < 0 or machine_field_mask & ~KNOWN_MACHINE_MASK:
        raise TelemetryCodecError(
            f"BAT4 machine field mask 0x{machine_field_mask:04x} must select only known fields."
        )
    size = 0
    if machine_field_mask & MACHINE_INTEGRITY:
        size += 4
    if machine_field_mask & MACHINE_ALIVE_BLOCK_COUNT:
        size += 4
    return size


def _record_dtype(field_mask: int) -> np.dtype[Any]:
    if isinstance(field_mask, bool) or not isinstance(field_mask, int):
        raise TypeError("field_mask must be an integer.")
    if field_mask < 0 or field_mask & ~KNOWN_FIELD_MASK:
        raise TelemetryCodecError(
            f"BAT4 field mask 0x{field_mask:04x} must select only known fields."
        )
    names = ["target_index", "valid"]
    formats: list[Any] = ["<u2", "u1"]
    offsets = [0, 2]
    offset = 3
    for bit, name, width in (
        (FIELD_POSITION, "position", 3),
        (FIELD_ROTATION, "rotation", 4),
        (FIELD_VELOCITY, "velocity", 3),
        (FIELD_ANGULAR_VELOCITY, "angular_velocity", 3),
    ):
        if field_mask & bit:
            names.append(name)
            formats.append(("<f4", (width,)))
            offsets.append(offset)
            offset += width * 4
    if field_mask & FIELD_FUEL:
        names.append("fuel")
        formats.append(("<f4", (2,)))
        offsets.append(offset)
        offset += 8
        names.append("fuel_valid")
        formats.append("u1")
        offsets.append(offset)
        offset += 4
    if field_mask & FIELD_FIRE:
        names.extend(("fire_burning", "fire_burned", "fire_valid"))
        formats.extend(("u1", "u1", "u1"))
        offsets.extend((offset, offset + 1, offset + 2))
        names.append("fire_intensity")
        formats.append("<f4")
        offsets.append(offset + 4)
        offset += 8
    if field_mask & FIELD_STEAM:
        names.extend(("steam", "steam_valid"))
        formats.extend(("u1", "u1"))
        offsets.extend((offset, offset + 1))
        offset += 4
    if field_mask & FIELD_ICE:
        names.extend(("ice", "ice_valid"))
        formats.extend(("u1", "u1"))
        offsets.extend((offset, offset + 1))
        offset += 4
    if field_mask & FIELD_HEALTH:
        names.append("health")
        formats.append(("<f4", (2,)))
        offsets.append(offset)
        offset += 8
        names.append("health_valid")
        formats.append("u1")
        offsets.append(offset)
        offset += 4
    if field_mask & FIELD_BUOYANCY:
        names.append("buoyancy")
        formats.append(("<f4", (3,)))
        offsets.append(offset)
        offset += 12
        names.append("buoyancy_valid")
        formats.append("u1")
        offsets.append(offset)
        offset += 4
    return np.dtype(
        {"names": names, "formats": formats, "offsets": offsets, "itemsize": offset}
    )


def record_size(field_mask: int) -> int:
    return _record_dtype(field_mask).itemsize


@dataclass(frozen=True)
class MachineState:
    field_mask: int
    machine_integrity: float | None
    alive_block_count: int | None

    def as_dict(self) -> dict[str, Any]:
        values: dict[str, Any] = {"field_mask": self.field_mask}
        if self.machine_integrity is not None:
            values["machine_integrity"] = self.machine_integrity
        if self.alive_block_count is not None:
            values["alive_block_count"] = self.alive_block_count
        return values


@dataclass(frozen=True)
class TelemetryMarker:
    sequence: int
    slot: int
    simulating: bool
    sequence_applied: int


@dataclass(frozen=True)
class TelemetryFrame:
    field_mask: int
    machine_field_mask: int
    sequence: int
    episode: int
    sequence_applied: int
    simulation_time: float
    slot: int
    simulating: bool
    error_code: int
    machine: MachineState
    records: np.ndarray[Any, np.dtype[Any]]
    buffer: bytes | bytearray | memoryview

    def get(self, key: str, default: Any = None) -> Any:
        values = {
            "sample_sequence": self.sequence,
            "episode": self.episode,
            "sequence_applied": self.sequence_applied,
            "simulation_time": self.simulation_time,
            "simulating": self.simulating,
            "field_mask": self.field_mask,
            "machine_field_mask": self.machine_field_mask,
            "machine": self.machine,
            "records": self.records,
        }
        return values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value


def encode_machine_section(
    *,
    machine_field_mask: int,
    machine_integrity: float | None = None,
    alive_block_count: int | None = None,
) -> bytes:
    expected = machine_section_size(machine_field_mask)
    payload = bytearray()
    if machine_field_mask & MACHINE_INTEGRITY:
        if machine_integrity is None or not math.isfinite(float(machine_integrity)):
            raise ValueError("machine_integrity must be a finite float.")
        payload.extend(struct.pack("<f", float(machine_integrity)))
    if machine_field_mask & MACHINE_ALIVE_BLOCK_COUNT:
        if (
            isinstance(alive_block_count, bool)
            or not isinstance(alive_block_count, int)
            or alive_block_count < 0
        ):
            raise ValueError("alive_block_count must be a non-negative integer.")
        payload.extend(struct.pack("<I", alive_block_count))
    if len(payload) != expected:
        raise TelemetryCodecError(
            f"machine section length {len(payload)} != expected {expected}."
        )
    return bytes(payload)


def decode_machine_section(
    data: bytes | bytearray | memoryview, machine_field_mask: int
) -> MachineState:
    view = memoryview(data)
    expected = machine_section_size(machine_field_mask)
    if view.nbytes != expected:
        raise TelemetryCodecError(
            f"BAT4 machine section length {view.nbytes}; expected {expected}."
        )
    offset = 0
    integrity = None
    alive = None
    if machine_field_mask & MACHINE_INTEGRITY:
        (integrity_value,) = struct.unpack_from("<f", view, offset)
        offset += 4
        if not math.isfinite(integrity_value):
            raise TelemetryCodecError("machine_integrity must be finite.")
        integrity = float(integrity_value)
    if machine_field_mask & MACHINE_ALIVE_BLOCK_COUNT:
        (alive,) = struct.unpack_from("<I", view, offset)
    return MachineState(
        field_mask=machine_field_mask,
        machine_integrity=integrity,
        alive_block_count=alive,
    )


def decode_telemetry_marker(data: bytes | bytearray | memoryview) -> TelemetryMarker:
    view = memoryview(data)
    if view.nbytes != MARKER_SIZE:
        raise TelemetryCodecError(
            f"Invalid BTM4 marker length {view.nbytes}; expected {MARKER_SIZE}."
        )
    magic, version, size, sequence, slot, simulating, reserved, applied = MARKER.unpack_from(view)
    if magic in (b"BTM3",):
        raise TelemetryCodecError(
            f"Unsupported protocol magic {magic!r}; v4 accepts only {MARKER_MAGIC!r}."
        )
    if magic != MARKER_MAGIC:
        raise TelemetryCodecError(
            f"Invalid BTM4 magic {magic!r}; expected {MARKER_MAGIC!r}."
        )
    if version != VERSION:
        raise TelemetryCodecError(f"Unsupported BTM4 version {version}; expected {VERSION}.")
    if size != MARKER_SIZE:
        raise TelemetryCodecError(f"Invalid BTM4 header size {size}; expected {MARKER_SIZE}.")
    if slot not in (0, 1) or simulating not in (0, 1) or reserved != 0:
        raise TelemetryCodecError("BTM4 contains an invalid slot, simulating flag, or reserved field.")
    return TelemetryMarker(sequence, slot, bool(simulating), applied)


def decode_telemetry(data: bytes | bytearray | memoryview) -> TelemetryFrame:
    view = memoryview(data)
    if view.nbytes < HEADER_SIZE:
        raise TelemetryCodecError(
            f"Truncated BAT4 header: got {view.nbytes} bytes, need {HEADER_SIZE}."
        )
    (
        magic,
        version,
        header_size,
        sequence,
        episode,
        sequence_applied,
        simulation_time,
        count,
        field_mask,
        encoded_record_size,
        slot,
        simulating,
        machine_field_mask,
        encoded_machine_size,
        reserved0,
        reserved1,
    ) = HEADER.unpack_from(view)
    if magic in (b"BAT3",):
        raise TelemetryCodecError(
            f"Unsupported protocol magic {magic!r}; v4 accepts only {MAGIC!r}."
        )
    if magic != MAGIC:
        raise TelemetryCodecError(f"Invalid BAT4 magic {magic!r}; expected {MAGIC!r}.")
    if version != VERSION:
        raise TelemetryCodecError(f"Unsupported BAT4 version {version}; expected {VERSION}.")
    if header_size != HEADER_SIZE:
        raise TelemetryCodecError(
            f"Invalid BAT4 header size {header_size}; expected {HEADER_SIZE}."
        )
    if reserved0 != 0 or reserved1 != 0:
        raise TelemetryCodecError("BAT4 reserved fields must be 0.")
    if field_mask == 0 and machine_field_mask == 0:
        raise TelemetryCodecError("BAT4 must select at least one target or machine field.")
    dtype = _record_dtype(field_mask)
    if encoded_record_size != dtype.itemsize:
        raise TelemetryCodecError(
            f"Invalid BAT4 record size {encoded_record_size}; field mask "
            f"0x{field_mask:04x} requires {dtype.itemsize}."
        )
    expected_machine = machine_section_size(machine_field_mask)
    if encoded_machine_size != expected_machine:
        raise TelemetryCodecError(
            f"Invalid BAT4 machine section size {encoded_machine_size}; expected {expected_machine}."
        )
    expected_size = HEADER_SIZE + encoded_machine_size + count * encoded_record_size
    if view.nbytes != expected_size:
        relation = "truncated" if view.nbytes < expected_size else "has trailing bytes"
        raise TelemetryCodecError(
            f"BAT4 payload {relation}: got {view.nbytes} bytes, expected {expected_size}."
        )
    if slot not in (0, 1) or simulating not in (0, 1):
        raise TelemetryCodecError("BAT4 slot and simulating flag must be 0 or 1.")
    if not math.isfinite(simulation_time):
        raise TelemetryCodecError("BAT4 simulation_time must be finite.")
    machine = decode_machine_section(
        view[HEADER_SIZE : HEADER_SIZE + encoded_machine_size],
        machine_field_mask,
    )
    records_offset = HEADER_SIZE + encoded_machine_size
    records = np.frombuffer(view, dtype=dtype, count=count, offset=records_offset)
    if len(records) and not _target_records_ok(records, dtype):
        raise TelemetryCodecError("BAT4 records contain an invalid status or non-finite value.")
    return TelemetryFrame(
        field_mask,
        machine_field_mask,
        sequence,
        episode,
        sequence_applied,
        float(simulation_time),
        slot,
        bool(simulating),
        0,
        machine,
        records,
        data,
    )


def encode_telemetry(
    records: Iterable[Mapping[str, Any]],
    *,
    field_mask: int,
    sequence: int,
    sequence_applied: int = 0,
    simulation_time: float = 0.0,
    simulating: bool = True,
    episode: int = 0,
    slot: int = 0,
    machine_field_mask: int = 0,
    machine_integrity: float | None = None,
    alive_block_count: int | None = None,
) -> bytes:
    rows = list(records)
    if field_mask == 0 and machine_field_mask == 0:
        raise TelemetryCodecError("BAT4 must select at least one target or machine field.")
    dtype = _record_dtype(field_mask)
    machine = encode_machine_section(
        machine_field_mask=machine_field_mask,
        machine_integrity=machine_integrity,
        alive_block_count=alive_block_count,
    )
    for label, value in (
        ("sequence", sequence),
        ("sequence_applied", sequence_applied),
        ("episode", episode),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{label} must be an integer.")
        minimum, maximum = (
            (-0x8000000000000000, 0x7FFFFFFFFFFFFFFF)
            if label == "sequence"
            else (-0x80000000, 0x7FFFFFFF)
        )
        if not minimum <= value <= maximum:
            raise ValueError(
                f"{label} must fit signed {'64' if label == 'sequence' else '32'}-bit."
            )
    if slot not in (0, 1):
        raise ValueError("slot must be 0 or 1.")
    if not math.isfinite(simulation_time):
        raise ValueError("simulation_time must be finite.")
    if len(rows) > 0xFFFF:
        raise ValueError("record count does not fit unsigned 16-bit.")
    array = np.zeros(len(rows), dtype=dtype)
    for position, row in enumerate(rows):
        target_index = row.get("target_index", row.get("block_index"))
        if (
            isinstance(target_index, bool)
            or not isinstance(target_index, int)
            or not 0 <= target_index <= 0xFFFF
        ):
            raise ValueError(f"record {position} target_index must fit unsigned 16-bit.")
        array["target_index"][position] = target_index
        valid = row.get("valid", 1)
        if valid not in (0, 1, False, True):
            raise ValueError(f"record {position} valid must be 0 or 1.")
        array["valid"][position] = int(bool(valid))
        for name in dtype.names[2:]:
            if name not in row and valid:
                raise ValueError(f"record {position} is missing enabled field {name!r}.")
            if name in row:
                _assign_record_field(array, position, name, row[name])
        if not _target_records_ok(array[position : position + 1], dtype):
            raise ValueError(f"record {position} has an invalid optional target field.")
    header = HEADER.pack(
        MAGIC,
        VERSION,
        HEADER_SIZE,
        sequence,
        episode,
        sequence_applied,
        float(simulation_time),
        len(rows),
        field_mask,
        dtype.itemsize,
        slot,
        int(simulating),
        machine_field_mask,
        len(machine),
        0,
        0,
    )
    return header + machine + array.tobytes()


def encode_telemetry_marker(
    *, sequence: int, slot: int, simulating: bool, sequence_applied: int
) -> bytes:
    if slot not in (0, 1):
        raise ValueError("slot must be 0 or 1.")
    return MARKER.pack(
        MARKER_MAGIC, VERSION, MARKER_SIZE, sequence, slot, int(simulating), 0,
        sequence_applied
    )


def _assign_record_field(
    array: np.ndarray[Any, np.dtype[Any]],
    position: int,
    name: str,
    raw: Any,
) -> None:
    if name in FLAG_RECORD_NAMES:
        if raw in (True, False):
            array[name][position] = 1 if raw else 0
            return
        if raw in (0, 1):
            array[name][position] = int(raw)
            return
        raise ValueError(f"record {position} field {name!r} must be 0 or 1.")
    values = np.asarray(raw, dtype=np.float32)
    if values.shape != array[name][position].shape or not np.isfinite(values).all():
        raise ValueError(f"record {position} field {name!r} has invalid shape or values.")
    array[name][position] = values


def _payload_finite(records: np.ndarray[Any, np.dtype[Any]], dtype: np.dtype[Any]) -> bool:
    names = dtype.names or ()
    return all(
        np.isfinite(records[name]).all()
        for name in names[2:]
        if name not in FLAG_RECORD_NAMES
    )


def _target_records_ok(
    records: np.ndarray[Any, np.dtype[Any]], dtype: np.dtype[Any]
) -> bool:
    if len(records) == 0 or not _flags_binary(records["valid"]):
        return len(records) == 0
    if not _payload_finite(records, dtype) or not _extra_records_ok(records, dtype):
        return False
    invalid = np.asarray(records["valid"]) == 0
    if not np.any(invalid):
        return True
    return all(
        not np.any(np.asarray(records[name])[invalid] != 0)
        for name in (dtype.names or ())[2:]
    )


def _flags_binary(values: Any) -> bool:
    flags = np.asarray(values)
    return not bool(np.any((flags != 0) & (flags != 1)))


def _optional_zero_when_invalid(
    records: np.ndarray[Any, np.dtype[Any]],
    valid_name: str,
    *value_names: str,
) -> bool:
    names = records.dtype.names or ()
    if valid_name not in names or len(records) == 0:
        return True
    flags = np.asarray(records[valid_name])
    if not _flags_binary(flags):
        return False
    invalid = flags == 0
    if not np.any(invalid):
        return True
    for name in value_names:
        if np.any(np.asarray(records[name])[invalid] != 0):
            return False
    return True


def _fuel_records_ok(records: np.ndarray[Any, np.dtype[Any]], dtype: np.dtype[Any]) -> bool:
    names = dtype.names or ()
    if "fuel" not in names or "fuel_valid" not in names or len(records) == 0:
        return True
    flags = np.asarray(records["fuel_valid"])
    if not _flags_binary(flags):
        return False
    fuel = np.asarray(records["fuel"])
    invalid = flags == 0
    if np.any(invalid) and np.any(fuel[invalid] != 0):
        return False
    valid = flags == 1
    if not np.any(valid):
        return True
    remaining = fuel[valid, 0]
    capacity = fuel[valid, 1]
    return bool(
        np.isfinite(remaining).all()
        and np.isfinite(capacity).all()
        and np.all(capacity > 0)
        and np.all(remaining >= 0)
        and np.all(remaining <= capacity)
    )


def _health_records_ok(records: np.ndarray[Any, np.dtype[Any]], dtype: np.dtype[Any]) -> bool:
    names = dtype.names or ()
    if "health" not in names or "health_valid" not in names or len(records) == 0:
        return True
    flags = np.asarray(records["health_valid"])
    if not _flags_binary(flags):
        return False
    health = np.asarray(records["health"])
    invalid = flags == 0
    if np.any(invalid) and np.any(health[invalid] != 0):
        return False
    valid = flags == 1
    if not np.any(valid):
        return True
    current = health[valid, 0]
    maximum = health[valid, 1]
    return bool(
        np.isfinite(current).all()
        and np.isfinite(maximum).all()
        and np.all(maximum > 0)
        and np.all(current >= 0)
        and np.all(current <= maximum)
    )


def _extra_records_ok(records: np.ndarray[Any, np.dtype[Any]], dtype: np.dtype[Any]) -> bool:
    if not _fuel_records_ok(records, dtype):
        return False
    if not _health_records_ok(records, dtype):
        return False
    names = dtype.names or ()
    if "fire_valid" in names:
        if not _flags_binary(records["fire_burning"]) or not _flags_binary(records["fire_burned"]):
            return False
        if not _optional_zero_when_invalid(
            records, "fire_valid", "fire_burning", "fire_burned", "fire_intensity"
        ):
            return False
    if "steam_valid" in names:
        if not _flags_binary(records["steam"]):
            return False
        if not _optional_zero_when_invalid(records, "steam_valid", "steam"):
            return False
    if "ice_valid" in names:
        if not _flags_binary(records["ice"]):
            return False
        if not _optional_zero_when_invalid(records, "ice_valid", "ice"):
            return False
    if "buoyancy_valid" in names:
        if not _optional_zero_when_invalid(records, "buoyancy_valid", "buoyancy"):
            return False
    return True
