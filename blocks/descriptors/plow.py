from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Four corners on the bottom OBB face (local -Y) plus farthest tip along working dir."""
    center_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    half_shape = np.asarray(self.geo.shape.virtual, dtype=np.float64).reshape(3)
    hx = float(half_shape[0])
    hy = float(half_shape[1])
    hz = float(half_shape[2])

    tip_dir_local = np.array([0.0, -1.0, 1.0], dtype=np.float64)
    tip_dir_world = rot_mat @ tip_dir_local
    tip_norm = float(np.linalg.norm(x=tip_dir_world))
    if tip_norm <= 1e-9:
        return "Plow tip direction is degenerate."
    tip_dir_unit = tip_dir_world / tip_norm

    bottom_locals = (
        np.array([hx, -hy, hz], dtype=np.float64),
        np.array([hx, -hy, -hz], dtype=np.float64),
        np.array([-hx, -hy, hz], dtype=np.float64),
        np.array([-hx, -hy, -hz], dtype=np.float64),
    )
    bottom_world = [center_virtual + rot_mat @ local for local in bottom_locals]
    corner_strings = [format_float_array(arr=Vector(vector=corner).real, precision=2) for corner in bottom_world]
    corners_text = "; ".join(corner_strings)

    tip_virtual = max(
        bottom_world,
        key=lambda corner: float(np.dot(corner - center_virtual, tip_dir_unit)),
    )
    tip_text = format_float_array(arr=Vector(vector=tip_virtual).real, precision=2)

    return (
        f"Plow bottom-face corner range : {corners_text}. "
        f"Plow tip (farthest along working direction on that face) at {tip_text}."
    )
