from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Report sail facing/propulsion direction and its world-horizontal projection."""
    if self.start_point is not None:
        sail_facing_virtual = np.asarray(a=self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        sail_facing_virtual = np.asarray(a=self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)

    facing_norm = float(np.linalg.norm(x=sail_facing_virtual))
    if facing_norm <= 1e-9:
        return "Sail propulsion direction is undefined (degenerate sail normal)."
    sail_facing_unit = sail_facing_virtual / facing_norm
    sail_facing_caption = Vector(vector=sail_facing_unit).caption

    horizontal = np.array(
        object=[float(sail_facing_unit[0]), float(sail_facing_unit[1]), 0.0],
        dtype=np.float64,
    )
    horizontal_norm = float(np.linalg.norm(x=horizontal))
    if horizontal_norm <= 1e-9:
        return (
            f"Sail facing/propulsion direction: {sail_facing_caption}. "
            "Horizontal propulsion component is zero (sail normal is near vertical)."
        )
    horizontal_unit = horizontal / horizontal_norm
    horizontal_caption = Vector(vector=horizontal_unit).caption
    return (
        f"Sail facing/propulsion direction: {sail_facing_caption}. "
        f"World-horizontal propulsion direction: {horizontal_caption}."
    )
