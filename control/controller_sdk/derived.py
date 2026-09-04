"""Out-of-game derived quantities from BAT4 position fields.

When a subscription does not include velocity, finite-difference estimates
can still be computed from published positions. There is no interpolation
and no gap-filling: a derivative is only reported once enough real samples
exist, and estimating across an episode boundary raises.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PositionSample:
    episode: int
    sample_sequence: int
    simulation_time: float
    x: float
    y: float
    z: float


class PositionHistory:
    """Fixed-window raw position history for one target block, fed from
    validated telemetry samples, with explicit finite-difference velocity.

    ``window`` is the number of most recent samples retained;
    ``velocity(span=n)`` computes (p[t] - p[t-n]) / (t - t_minus_n) over the
    recorded simulation times of real samples. Both the window and the span
    are the caller's explicit configuration - nothing is defaulted from the
    sample rate, and missing history raises instead of returning zeros.
    """

    def __init__(self, guid: str, *, window: int):
        if window < 2:
            raise ValueError("PositionHistory window must be >= 2 samples.")
        self.guid = guid.lower()
        self.window = window
        self._samples: deque[PositionSample] = deque(maxlen=window)

    def push_sample(self, sample: dict[str, Any]) -> PositionSample:
        """Append this target's position from one validated telemetry
        sample. Duplicate sample_sequences (the same published sample read
        twice) are ignored; an episode change clears the history because
        positions across a simulation restart are not one trajectory."""
        target = None
        for row in sample.get("targets", []):
            if str(row.get("guid", "")).lower() == self.guid:
                target = row
                break
        if target is None:
            raise KeyError(
                f"Telemetry sample carries no target {self.guid!r}; "
                f"targets={[row.get('guid') for row in sample.get('targets', [])]}"
            )
        record = PositionSample(
            episode=int(sample["episode"]),
            sample_sequence=int(sample["sample_sequence"]),
            simulation_time=float(sample["simulation_time"]),
            x=float(target["x"]),
            y=float(target["y"]),
            z=float(target["z"]),
        )
        if self._samples and self._samples[-1].episode != record.episode:
            self._samples.clear()
        if self._samples and record.sample_sequence == self._samples[-1].sample_sequence:
            return self._samples[-1]
        if self._samples and record.sample_sequence < self._samples[-1].sample_sequence:
            raise ValueError(
                f"sample_sequence went backwards for target {self.guid!r}: "
                f"{self._samples[-1].sample_sequence} -> {record.sample_sequence}"
            )
        self._samples.append(record)
        return record

    def push_target(
        self,
        *,
        episode: int,
        sample_sequence: int,
        simulation_time: float,
        position: tuple[float, float, float],
    ) -> PositionSample:
        return self.push_sample(
            {
                "episode": episode,
                "sample_sequence": sample_sequence,
                "simulation_time": simulation_time,
                "targets": [
                    {
                        "guid": self.guid,
                        "x": position[0],
                        "y": position[1],
                        "z": position[2],
                    }
                ],
            }
        )

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def latest(self) -> PositionSample:
        if not self._samples:
            raise ValueError(f"No position history recorded yet for target {self.guid!r}.")
        return self._samples[-1]

    def velocity(self, *, span: int = 1) -> tuple[float, float, float]:
        """Finite-difference velocity over the last ``span`` retained
        samples: (p[-1] - p[-1-span]) / (t[-1] - t[-1-span]). Raises when
        the history does not yet contain span+1 samples - it never fills in
        zeros for missing data."""
        if span < 1:
            raise ValueError("velocity span must be >= 1.")
        if len(self._samples) < span + 1:
            raise ValueError(
                f"velocity(span={span}) needs {span + 1} samples; history has {len(self._samples)}."
            )
        newest = self._samples[-1]
        oldest = self._samples[-1 - span]
        dt = newest.simulation_time - oldest.simulation_time
        if dt <= 0.0:
            raise ValueError(
                f"Non-positive time delta {dt} between samples "
                f"{oldest.sample_sequence} and {newest.sample_sequence}."
            )
        return (
            (newest.x - oldest.x) / dt,
            (newest.y - oldest.y) / dt,
            (newest.z - oldest.z) / dt,
        )
