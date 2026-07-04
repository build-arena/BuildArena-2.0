from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Report paddle-face normal (perpendicular to blade plane) from the thickness axis."""
    shape_virtual = np.asarray(a=self.geo.shape.virtual, dtype=np.float64).reshape(3)
    normal_axis_idx = int(np.argmin(a=np.abs(shape_virtual)))
    normal_local = np.zeros(shape=3, dtype=np.float64)
    normal_local[normal_axis_idx] = 1.0
    normal_world = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64) @ normal_local
    normal_norm = float(np.linalg.norm(x=normal_world))
    if normal_norm <= 1e-9:
        return "Paddle-face normal direction is undefined (degenerate thickness axis)."
    normal_unit = normal_world / normal_norm
    normal_caption = Vector(vector=normal_unit).caption
    return f"Current paddle-face normal direction (perpendicular to the blade plane): {normal_caption}."
