from __future__ import annotations

from itertools import product

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Planform corners plus chord, span, and the (two-sided) wing-plane normal.

    The wing is a flat plate that lies in its chord-span plane (thin axis = local +Y).
    Aerodynamic lift/drag come from the airflow component along that wing-plane normal,
    and both faces behave identically, so the normal is reported as an axis (both
    directions). Reporting the normal also reveals when two mirrored wings have their
    surfaces facing opposite ways and whether the surface is horizontal or vertical.
    """
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    half_shape = np.asarray(self.geo.shape.virtual, dtype=np.float64).reshape(3) * 0.5
    hx, _, hz = float(half_shape[0]), float(half_shape[1]), float(half_shape[2])
    center_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)

    corner_strings: list[str] = []
    for sx, sz in product([-1, 1], [-1, 1]):
        local_offset = np.array([sx * hx, 0.0, sz * hz], dtype=np.float64)
        corner_virtual = center_virtual + rot_mat @ local_offset
        corner_real = Vector(vector=corner_virtual).real
        corner_strings.append(format_float_array(arr=corner_real, precision=2))

    chord_world = rot_mat @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
    span_world = rot_mat @ np.array([1.0, 0.0, 0.0], dtype=np.float64)
    normal_world = rot_mat @ np.array([0.0, 1.0, 0.0], dtype=np.float64)

    chord_caption = Vector(vector=chord_world).caption
    span_caption = Vector(vector=span_world).caption
    normal_caption = Vector(vector=normal_world).caption
    normal_caption_opp = Vector(vector=-normal_world).caption

    corners_joined = "; ".join(corner_strings)
    return (
        f"Wing planform corners : {corners_joined}. "
        f"Leading-to-trailing chord direction (local +Z): {chord_caption}. "
        f"Span direction (local +X): {span_caption}. "
        f"Wing-plane normal / lift-drag axis (local +Y, two-sided): "
        f"{normal_caption} and {normal_caption_opp} (the two faces are aerodynamically equivalent)."
    )
