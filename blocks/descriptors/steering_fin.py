from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


SHORT_SIDE_LENGTH = 0.6
LONG_SIDE_LENGTH = 1.3
SURFACE_CHORD = 1.0


def descriptor(self: Block) -> str:
    """Report steering-fin long/short side directions and corner coordinates."""
    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)
    chord_raw = rot_mat @ np.array(object=[0.0, 0.0, 1.0], dtype=np.float64)
    chord_norm = float(np.linalg.norm(x=chord_raw))
    if chord_norm <= 1e-9:
        return "Steering-fin surface chord is undefined (degenerate rotation)."
    chord_unit = chord_raw / chord_norm

    if self.start_point is not None:
        axis_origin = np.asarray(a=self.start_point.center.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(a=self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        axis_origin = np.asarray(a=self.center_pos.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(a=self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)

    normal_norm = float(np.linalg.norm(x=normal_raw))
    if normal_norm <= 1e-9:
        return "Steering-fin install axis is undefined (degenerate mount normal)."
    normal_unit = normal_raw / normal_norm

    short_axis = axis_origin + SHORT_SIDE_LENGTH * normal_unit
    long_axis = axis_origin - LONG_SIDE_LENGTH * normal_unit
    short_outer = short_axis + SURFACE_CHORD * chord_unit
    long_outer = long_axis + SURFACE_CHORD * chord_unit

    corners = [
        ("short-axis", short_axis),
        ("long-axis", long_axis),
        ("short-outer", short_outer),
        ("long-outer", long_outer),
    ]
    corner_text = "; ".join(
        f"{name}={format_float_array(arr=Vector(vector=corner).real, precision=2)}"
        for name, corner in corners
    )
    short_side_caption = Vector(vector=normal_unit).caption
    long_side_caption = Vector(vector=-normal_unit).caption
    return (
        f"Steering-fin short side extends {SHORT_SIDE_LENGTH} units toward "
        f"{short_side_caption} from the mount face center; long side extends "
        f"{LONG_SIDE_LENGTH} units toward {long_side_caption}. "
        f"Trapezoid fin corners: {corner_text}."
    )
