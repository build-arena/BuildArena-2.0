from __future__ import annotations

from itertools import product

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def _plate_axes_on_plane(*, rot_mat: np.ndarray, n_unit: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Long = local +X, width = local +Y, both projected onto the mount plane."""
    ex_world = rot_mat @ np.array([1.0, 0.0, 0.0], dtype=np.float64)
    ey_world = rot_mat @ np.array([0.0, 1.0, 0.0], dtype=np.float64)
    long_proj = ex_world - float(np.dot(ex_world, n_unit)) * n_unit
    wide_raw = ey_world - float(np.dot(ey_world, n_unit)) * n_unit
    long_norm = float(np.linalg.norm(x=long_proj))
    if long_norm <= 1e-9:
        raise ValueError("Could not project plate long axis onto mount plane.")
    u_long = long_proj / long_norm
    wide_orth = wide_raw - float(np.dot(wide_raw, u_long)) * u_long
    wide_norm = float(np.linalg.norm(x=wide_orth))
    if wide_norm <= 1e-9:
        cross = np.cross(n_unit, u_long)
        wide_norm = float(np.linalg.norm(x=cross))
        if wide_norm <= 1e-9:
            raise ValueError("Could not build plate width axis on mount plane.")
        v_wide = cross / wide_norm
    else:
        v_wide = wide_orth / wide_norm
    return u_long, v_wide


def descriptor(self: Block) -> str:
    """2x1 footprint on the attach face, centered at the mount point (like small armor)."""
    if self.start_point is None:
        return "Coverage footprint is undefined until the plate is attached to a face."

    half_long = 1.0
    half_wide = 0.5
    n_virtual = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    n_norm = float(np.linalg.norm(x=n_virtual))
    if n_norm <= 1e-9:
        return "Coverage footprint is undefined (degenerate mount normal)."
    n_unit = n_virtual / n_norm
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    u_long, v_wide = _plate_axes_on_plane(rot_mat=rot_mat, n_unit=n_unit)
    center_virtual = np.asarray(self.start_point.center.virtual, dtype=np.float64).reshape(3)

    corner_strings: list[str] = []
    for du, dv in product([-1, 1], [-1, 1]):
        corner_virtual = center_virtual + du * half_long * u_long + dv * half_wide * v_wide
        corner_real = Vector(vector=corner_virtual).real
        corner_strings.append(format_float_array(arr=corner_real, precision=2))

    joined = "; ".join(corner_strings)
    return f"Coverage on attach face (2x1, four corner coordinates): {joined}."
