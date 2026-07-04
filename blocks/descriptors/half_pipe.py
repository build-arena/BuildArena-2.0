from __future__ import annotations

from itertools import product

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Four corners on the OBB face at +half along the shortest extent; barrel axis = median extent."""
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    center_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
    ext = np.asarray(self.geo.shape.virtual, dtype=np.float64).reshape(3)
    he = ext * 0.5

    short_idx = int(np.argmin(a=ext))
    pipe_idx = int(np.argsort(a=ext, kind="stable")[1])
    others = [i for i in range(3) if i != short_idx]
    i0, i1 = int(others[0]), int(others[1])

    corner_strings: list[str] = []
    for s0, s1 in product([-1, 1], [-1, 1]):
        local = np.zeros(shape=3, dtype=np.float64)
        local[short_idx] = float(he[short_idx])
        local[i0] = float(s0 * he[i0])
        local[i1] = float(s1 * he[i1])
        world = center_virtual + rot_mat @ local
        corner_strings.append(format_float_array(arr=Vector(vector=world).real, precision=2))

    axis_local = np.zeros(shape=3, dtype=np.float64)
    axis_local[pipe_idx] = 1.0
    axis_world = rot_mat @ axis_local
    axis_caption = Vector(vector=axis_world).caption

    corners_joined = "; ".join(corner_strings)
    return (
        f"Rim-adjacent body surface corner coordinates : {corners_joined}. "
        f"Half-pipe barrel axis direction (median-length local axis): {axis_caption}."
    )
