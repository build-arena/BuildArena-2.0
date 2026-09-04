"""Strict BAA4 fixed-record action codec.

v4 has no doorbell marker. Each sequence is an immutable file.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterable

from .protocol import ACTION_MAGIC, PROTOCOL_VERSION

MAGIC = ACTION_MAGIC
VERSION = PROTOCOL_VERSION
HEADER = struct.Struct("<4sHHiiHH")
RECORD = struct.Struct("<Hf")
HEADER_SIZE = HEADER.size
RECORD_SIZE = RECORD.size


class ActionCodecError(ValueError):
    """A BAA4 payload is malformed or incompatible."""


@dataclass(frozen=True)
class ActionRecord:
    channel_index: int
    value: float


@dataclass(frozen=True)
class ActionFrame:
    sequence: int
    keepalive: int
    records: tuple[ActionRecord, ...]


def _validate_record(channel_index: int, value: float, position: int) -> None:
    if isinstance(channel_index, bool) or not isinstance(channel_index, int):
        raise TypeError(f"action record {position} channel_index must be an integer.")
    if not 0 <= channel_index <= 0xFFFF:
        raise ValueError(
            f"action record {position} channel_index must fit unsigned 16-bit."
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"action record {position} value must be numeric.")
    if not math.isfinite(float(value)):
        raise ValueError(f"action record {position} value must be finite.")
    try:
        packed = struct.pack("<f", float(value))
    except (OverflowError, struct.error) as exc:
        raise ValueError(
            f"action record {position} value {value!r} does not fit float32."
        ) from exc
    if not math.isfinite(struct.unpack("<f", packed)[0]):
        raise ValueError(
            f"action record {position} value {value!r} does not fit finite float32."
        )


def encode_action(
    records: Iterable[ActionRecord | tuple[int, float]],
    *,
    sequence: int,
    keepalive: int = 0,
) -> bytes:
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise TypeError("sequence must be an integer.")
    if not 1 <= sequence <= 0x7FFFFFFF:
        raise ValueError("sequence must be a positive signed 32-bit integer.")
    if isinstance(keepalive, bool) or not isinstance(keepalive, int):
        raise TypeError("keepalive must be an integer token.")
    if not -0x80000000 <= keepalive <= 0x7FFFFFFF:
        raise ValueError("keepalive must fit signed 32-bit.")
    normalized: list[tuple[int, float]] = []
    for position, item in enumerate(records):
        if isinstance(item, ActionRecord):
            channel_index, value = item.channel_index, item.value
        else:
            try:
                channel_index, value = item
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"action record {position} must be ActionRecord or (index, value)."
                ) from exc
        _validate_record(channel_index, value, position)
        normalized.append((channel_index, float(value)))
    if len(normalized) > 0xFFFF:
        raise ValueError("record count does not fit unsigned 16-bit.")
    payload = bytearray(
        HEADER.pack(
            MAGIC, VERSION, HEADER_SIZE, sequence, keepalive, len(normalized), RECORD_SIZE
        )
    )
    for channel_index, value in normalized:
        payload.extend(RECORD.pack(channel_index, value))
    return bytes(payload)


def decode_action(data: bytes | bytearray | memoryview) -> ActionFrame:
    view = memoryview(data)
    if view.nbytes < HEADER_SIZE:
        raise ActionCodecError(
            f"Truncated BAA4 header: got {view.nbytes} bytes, need {HEADER_SIZE}."
        )
    magic, version, header_size, sequence, keepalive, count, record_size = HEADER.unpack_from(
        view
    )
    if magic in (b"BAA3", b"BAQ3"):
        raise ActionCodecError(
            f"Unsupported protocol magic {magic!r}; v4 accepts only {MAGIC!r}."
        )
    if magic != MAGIC:
        raise ActionCodecError(f"Invalid BAA4 magic {magic!r}; expected {MAGIC!r}.")
    if version != VERSION:
        raise ActionCodecError(
            f"Unsupported BAA4 version {version}; expected {VERSION}."
        )
    if header_size != HEADER_SIZE:
        raise ActionCodecError(
            f"Invalid BAA4 header size {header_size}; expected {HEADER_SIZE}."
        )
    if record_size != RECORD_SIZE:
        raise ActionCodecError(
            f"Invalid BAA4 record size {record_size}; expected {RECORD_SIZE}."
        )
    expected_size = header_size + count * record_size
    if view.nbytes != expected_size:
        relation = "truncated" if view.nbytes < expected_size else "has trailing bytes"
        raise ActionCodecError(
            f"BAA4 payload {relation}: got {view.nbytes} bytes, expected {expected_size}."
        )
    records = tuple(
        ActionRecord(*RECORD.unpack_from(view, header_size + index * record_size))
        for index in range(count)
    )
    for position, record in enumerate(records):
        if not math.isfinite(record.value):
            raise ActionCodecError(f"BAA4 record {position} value must be finite.")
    return ActionFrame(
        sequence=sequence,
        keepalive=keepalive,
        records=records,
    )
