"""Strict BAB4/BBM4 bulk-batch codec matching the ToolKit wire format."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .protocol import (
    BULK_MAGIC,
    BULK_MARKER_MAGIC,
    BULK_MAX_BATCH_SAMPLES,
    PROTOCOL_VERSION,
)
from .telemetry_codec import (
    MachineState,
    _assign_record_field,
    _record_dtype,
    _target_records_ok,
    decode_machine_section,
    encode_machine_section,
    machine_section_size,
)

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - broken installation only
    raise ImportError(
        "controller_sdk BAB4 bulk support requires NumPy; run `uv sync`. "
        "No non-NumPy fallback is provided."
    ) from exc

VERSION = PROTOCOL_VERSION

# magic, version, header_size, batch_sequence, episode, frame_count,
# record_count, target_mask, record_size, slot, simulating,
# machine_mask, machine_section_size, reserved
BATCH_HEADER = struct.Struct("<4sHHqiHHHHBBHHHI")
BATCH_HEADER_SIZE = 40

FRAME_HEADER = struct.Struct("<qfi")
FRAME_HEADER_SIZE = 16

MARKER = struct.Struct("<4sHHqBBHi")
MARKER_SIZE = 24


class BulkCodecError(ValueError):
    """A BAB4/BBM4 payload is malformed or incompatible."""


@dataclass(frozen=True)
class BulkMarker:
    batch_sequence: int
    slot: int
    simulating: bool
    frame_count: int


@dataclass(frozen=True)
class BulkFrame:
    sample_sequence: int
    simulation_time: float
    machine: MachineState
    records: "np.ndarray[Any, np.dtype[Any]]"


@dataclass(frozen=True)
class BulkBatch:
    batch_sequence: int
    episode: int
    field_mask: int
    machine_field_mask: int
    record_count: int
    slot: int
    simulating: bool
    frames: tuple[BulkFrame, ...]
    buffer: bytes | bytearray | memoryview


def decode_bulk_marker(data: bytes | bytearray | memoryview) -> BulkMarker:
    view = memoryview(data)
    if view.nbytes != MARKER_SIZE:
        raise BulkCodecError(
            f"Invalid BBM4 marker length {view.nbytes}; expected {MARKER_SIZE}."
        )
    magic, version, size, sequence, slot, simulating, frame_count, reserved = (
        MARKER.unpack_from(view)
    )
    if magic in (b"BBM3",):
        raise BulkCodecError(
            f"Unsupported protocol magic {magic!r}; v4 accepts only {BULK_MARKER_MAGIC!r}."
        )
    if magic != BULK_MARKER_MAGIC:
        raise BulkCodecError(
            f"Invalid BBM4 magic {magic!r}; expected {BULK_MARKER_MAGIC!r}."
        )
    if version != VERSION:
        raise BulkCodecError(f"Unsupported BBM4 version {version}; expected {VERSION}.")
    if size != MARKER_SIZE:
        raise BulkCodecError(f"Invalid BBM4 header size {size}; expected {MARKER_SIZE}.")
    if slot not in (0, 1) or simulating not in (0, 1) or reserved != 0:
        raise BulkCodecError(
            "BBM4 contains an invalid slot, simulating flag, or reserved field."
        )
    if frame_count < 1:
        raise BulkCodecError("BBM4 frame_count must be at least 1.")
    return BulkMarker(sequence, slot, bool(simulating), frame_count)


def decode_bulk_batch(data: bytes | bytearray | memoryview) -> BulkBatch:
    view = memoryview(data)
    if view.nbytes < BATCH_HEADER_SIZE:
        raise BulkCodecError(
            f"Truncated BAB4 header: got {view.nbytes} bytes, need {BATCH_HEADER_SIZE}."
        )
    (
        magic,
        version,
        header_size,
        batch_sequence,
        episode,
        frame_count,
        record_count,
        field_mask,
        encoded_record_size,
        slot,
        simulating,
        machine_field_mask,
        encoded_machine_size,
        reserved16,
        reserved,
    ) = BATCH_HEADER.unpack_from(view)
    if magic in (b"BAB3",):
        raise BulkCodecError(
            f"Unsupported protocol magic {magic!r}; v4 accepts only {BULK_MAGIC!r}."
        )
    if magic != BULK_MAGIC:
        raise BulkCodecError(f"Invalid BAB4 magic {magic!r}; expected {BULK_MAGIC!r}.")
    if version != VERSION:
        raise BulkCodecError(f"Unsupported BAB4 version {version}; expected {VERSION}.")
    if header_size != BATCH_HEADER_SIZE:
        raise BulkCodecError(
            f"Invalid BAB4 header size {header_size}; expected {BATCH_HEADER_SIZE}."
        )
    if reserved != 0 or reserved16 != 0:
        raise BulkCodecError("BAB4 reserved header field must be 0.")
    if slot not in (0, 1) or simulating not in (0, 1):
        raise BulkCodecError("BAB4 slot and simulating flag must be 0 or 1.")
    if frame_count < 1:
        raise BulkCodecError("BAB4 frame_count must be at least 1.")
    if field_mask == 0 and machine_field_mask == 0:
        raise BulkCodecError("BAB4 must select at least one target or machine field.")
    dtype = _record_dtype(field_mask)
    if encoded_record_size != dtype.itemsize:
        raise BulkCodecError(
            f"Invalid BAB4 record size {encoded_record_size}; field mask "
            f"0x{field_mask:04x} requires {dtype.itemsize}."
        )
    expected_machine = machine_section_size(machine_field_mask)
    if encoded_machine_size != expected_machine:
        raise BulkCodecError(
            f"Invalid BAB4 machine section size {encoded_machine_size}; expected {expected_machine}."
        )
    frame_size = FRAME_HEADER_SIZE + encoded_machine_size + record_count * encoded_record_size
    expected_size = BATCH_HEADER_SIZE + frame_count * frame_size
    if view.nbytes != expected_size:
        relation = "truncated" if view.nbytes < expected_size else "has trailing bytes"
        raise BulkCodecError(
            f"BAB4 payload {relation}: got {view.nbytes} bytes, expected {expected_size}."
        )
    frames: list[BulkFrame] = []
    previous_sequence: int | None = None
    for position in range(frame_count):
        offset = BATCH_HEADER_SIZE + position * frame_size
        sample_sequence, simulation_time, frame_reserved = FRAME_HEADER.unpack_from(
            view, offset
        )
        if frame_reserved != 0:
            raise BulkCodecError(f"BAB4 frame {position} reserved field must be 0.")
        if not math.isfinite(simulation_time):
            raise BulkCodecError(f"BAB4 frame {position} simulation_time must be finite.")
        if previous_sequence is not None and sample_sequence != previous_sequence + 1:
            raise BulkCodecError(
                f"BAB4 frame sequences must be contiguous: "
                f"{previous_sequence} -> {sample_sequence}."
            )
        previous_sequence = sample_sequence
        machine = decode_machine_section(
            view[offset + FRAME_HEADER_SIZE : offset + FRAME_HEADER_SIZE + encoded_machine_size],
            machine_field_mask,
        )
        records = np.frombuffer(
            view,
            dtype=dtype,
            count=record_count,
            offset=offset + FRAME_HEADER_SIZE + encoded_machine_size,
        )
        if len(records) and not _target_records_ok(records, dtype):
            raise BulkCodecError(
                f"BAB4 frame {position} contains an invalid status or non-finite value."
            )
        frames.append(BulkFrame(sample_sequence, float(simulation_time), machine, records))
    return BulkBatch(
        batch_sequence,
        episode,
        field_mask,
        machine_field_mask,
        record_count,
        slot,
        bool(simulating),
        tuple(frames),
        data,
    )


def encode_bulk_batch(
    frames: Sequence[Mapping[str, Any]],
    *,
    field_mask: int,
    batch_sequence: int,
    episode: int = 0,
    slot: int = 0,
    simulating: bool = True,
    machine_field_mask: int = 0,
) -> bytes:
    if not 1 <= len(frames) <= BULK_MAX_BATCH_SAMPLES:
        raise ValueError(
            f"BAB4 batch must carry 1..{BULK_MAX_BATCH_SAMPLES} frames."
        )
    if slot not in (0, 1):
        raise ValueError("slot must be 0 or 1.")
    if field_mask == 0 and machine_field_mask == 0:
        raise ValueError("BAB4 must select at least one target or machine field.")
    for label, value, bits in (
        ("batch_sequence", batch_sequence, 64),
        ("episode", episode, 32),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{label} must be an integer.")
        limit = 1 << (bits - 1)
        if not -limit <= value < limit:
            raise ValueError(f"{label} must fit signed {bits}-bit.")
    dtype = _record_dtype(field_mask)
    encoded_machine_size = machine_section_size(machine_field_mask)
    encoded_frames: list[bytes] = []
    record_count: int | None = None
    previous_sequence: int | None = None
    for position, frame in enumerate(frames):
        sample_sequence = frame.get("sample_sequence")
        simulation_time = frame.get("simulation_time", 0.0)
        rows = list(frame.get("records", ()))
        if isinstance(sample_sequence, bool) or not isinstance(sample_sequence, int):
            raise ValueError(f"frame {position} sample_sequence must be an integer.")
        if previous_sequence is not None and sample_sequence != previous_sequence + 1:
            raise ValueError("BAB4 frame sequences must be contiguous.")
        previous_sequence = sample_sequence
        if not math.isfinite(float(simulation_time)):
            raise ValueError(f"frame {position} simulation_time must be finite.")
        if record_count is None:
            record_count = len(rows)
            if record_count > 0xFFFF:
                raise ValueError("record count must fit 0..65535.")
        elif len(rows) != record_count:
            raise ValueError("all BAB4 frames must carry the same record count.")
        machine = encode_machine_section(
            machine_field_mask=machine_field_mask,
            machine_integrity=frame.get("machine_integrity"),
            alive_block_count=frame.get("alive_block_count"),
        )
        if len(machine) != encoded_machine_size:
            raise ValueError(f"frame {position} machine section length mismatch.")
        array = np.zeros(record_count, dtype=dtype)
        for row_position, row in enumerate(rows):
            target_index = row.get("target_index")
            if (
                isinstance(target_index, bool)
                or not isinstance(target_index, int)
                or not 0 <= target_index <= 0xFFFF
            ):
                raise ValueError(
                    f"frame {position} record {row_position} target_index must fit unsigned 16-bit."
                )
            array["target_index"][row_position] = target_index
            valid = row.get("valid", 1)
            if valid not in (0, 1, False, True):
                raise ValueError(
                    f"frame {position} record {row_position} valid must be 0 or 1."
                )
            array["valid"][row_position] = int(bool(valid))
            for name in dtype.names[2:]:
                if name not in row and valid:
                    raise ValueError(
                        f"frame {position} record {row_position} is missing field {name!r}."
                    )
                if name in row:
                    _assign_record_field(array, row_position, name, row[name])
            if not _target_records_ok(array[row_position : row_position + 1], dtype):
                raise ValueError(
                    f"frame {position} record {row_position} has an invalid optional target field."
                )
        encoded_frames.append(
            FRAME_HEADER.pack(sample_sequence, float(simulation_time), 0)
            + machine
            + array.tobytes()
        )
    assert record_count is not None
    header = BATCH_HEADER.pack(
        BULK_MAGIC,
        VERSION,
        BATCH_HEADER_SIZE,
        batch_sequence,
        episode,
        len(frames),
        record_count,
        field_mask,
        dtype.itemsize,
        slot,
        int(simulating),
        machine_field_mask,
        encoded_machine_size,
        0,
        0,
    )
    return header + b"".join(encoded_frames)


def encode_bulk_marker(
    *, batch_sequence: int, slot: int, simulating: bool, frame_count: int
) -> bytes:
    if slot not in (0, 1):
        raise ValueError("slot must be 0 or 1.")
    if not 1 <= frame_count <= 0xFFFF:
        raise ValueError("frame_count must fit 1..65535.")
    return MARKER.pack(
        BULK_MARKER_MAGIC,
        VERSION,
        MARKER_SIZE,
        batch_sequence,
        slot,
        int(simulating),
        frame_count,
        0,
    )
