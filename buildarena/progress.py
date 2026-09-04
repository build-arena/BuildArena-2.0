"""Shared tqdm helpers. Disabled when stderr is not a TTY (CI, redirected logs)."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterable, Iterator
from typing import TypeVar

from tqdm import tqdm

T = TypeVar("T")


def tqdm_enabled() -> bool:
    return sys.stderr.isatty()


def progress_iter(
    items: Iterable[T],
    *,
    desc: str,
    unit: str,
    total: int | None = None,
) -> Iterator[T]:
    yield from tqdm(
        items,
        desc=desc,
        unit=unit,
        total=total,
        disable=not tqdm_enabled(),
        dynamic_ncols=True,
    )


class TimeBudgetBar:
    """Elapsed-time bar against a timeout. Not real work progress."""

    def __init__(self, *, timeout: float, desc: str):
        self.timeout = max(1.0, float(timeout))
        self.desc = desc
        self.started = time.monotonic()
        self.bar = tqdm(
            total=int(round(self.timeout)),
            desc=desc,
            unit="s",
            disable=not tqdm_enabled(),
            dynamic_ncols=True,
            leave=True,
        )
        if not tqdm_enabled():
            print(f"{desc} (timeout {self.timeout:.0f}s)...", flush=True)

    def poke(self, detail: str = "") -> None:
        elapsed = time.monotonic() - self.started
        target = min(int(elapsed), self.bar.total or 0)
        if target > self.bar.n:
            self.bar.update(target - self.bar.n)
        if detail:
            self.bar.set_postfix_str(detail, refresh=False)
        self.bar.refresh()

    def close(self, *, ok: bool) -> None:
        elapsed = time.monotonic() - self.started
        if ok and self.bar.total is not None:
            remaining = self.bar.total - self.bar.n
            if remaining > 0:
                self.bar.update(remaining)
        self.bar.set_postfix_str("ready" if ok else "timeout", refresh=False)
        self.bar.close()
        if not tqdm_enabled():
            status = "ready" if ok else "timed out"
            print(f"{self.desc} {status} after {elapsed:.0f}s.", flush=True)
