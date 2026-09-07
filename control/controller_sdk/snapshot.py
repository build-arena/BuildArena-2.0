"""Bounded reads of the v4 double-buffer commit protocol."""

from __future__ import annotations

import errno
import math
import time
from pathlib import Path
from typing import Any, Callable

from .protocol import read_shared_bytes


class SnapshotUnavailableError(TimeoutError):
    """Temporary contention prevented a consistent snapshot within the budget.

    This is retryable within the caller's deadline. Codec/version errors and
    permanent I/O failures are deliberately not wrapped in this exception.
    """


def new_read_stats() -> dict[str, Any]:
    return dict(reads=0, retries=0, unavailable=0, last_read_seconds=0.0,
                max_read_seconds=0.0, last_retry_reason=None)


def retryable_io(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in (2, 3, 32, 33)  # Not yet published, sharing/lock violation.
    return exc.errno in (errno.ENOENT, errno.EAGAIN, errno.EBUSY, errno.EINTR)


def read_committed(
    marker_path: Path, buffer_paths: tuple[Path, Path], *, timeout: float,
    decode_marker: Callable, decode_payload: Callable, matches: Callable,
    stats: dict[str, Any], label: str,
) -> Any:
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Snapshot timeout must be a finite non-negative number.")
    started = time.monotonic()
    deadline = started + timeout
    attempt = 0
    reason = ""
    stats["reads"] += 1
    try:
        while True:
            attempt += 1
            try:
                marker = decode_marker(read_shared_bytes(marker_path))
                payload = read_shared_bytes(buffer_paths[marker.slot])
                marker_after = decode_marker(read_shared_bytes(marker_path))
                if marker_after != marker:
                    reason = f"{label} publish marker changed during the read"
                else:
                    # Do not diagnose an abandoned, concurrently overwritten
                    # slot as corruption. Decode only under a stable marker.
                    frame = decode_payload(payload)
                    if matches(frame, marker):
                        return frame
                    reason = f"{label} buffer does not match its commit marker"
            except OSError as exc:
                if not retryable_io(exc):
                    raise
                reason = f"{label} publication is temporarily unavailable: {exc}"
            stats["last_retry_reason"] = reason
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stats["unavailable"] += 1
                raise SnapshotUnavailableError(
                    f"No consistent {label} snapshot within {timeout:.3f}s "
                    f"({attempt} attempts): {reason}"
                )
            stats["retries"] += 1
            time.sleep(min(0.001 * min(attempt, 4), remaining))
    finally:
        elapsed = time.monotonic() - started
        stats["last_read_seconds"] = elapsed
        stats["max_read_seconds"] = max(stats["max_read_seconds"], elapsed)
