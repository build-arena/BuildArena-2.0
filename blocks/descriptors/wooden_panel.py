from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def _plane_basis(*, plane_normal_virtual: np.ndarray, rot_mat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n_vec = np.asarray(plane_normal_virtual, dtype=np.float64).reshape(3)
    n_norm = np.linalg.norm(n_vec)
    if n_norm <= 1e-9:
        raise ValueError("Mount face normal is degenerate.")
    n_unit = n_vec / n_norm

    for column_index in range(3):
        axis = np.asarray(rot_mat[:, column_index], dtype=np.float64).reshape(3)
        u = axis - float(np.dot(axis, n_unit)) * n_unit
        u_norm = np.linalg.norm(u)
        if u_norm > 1e-6:
            u_unit = u / u_norm
            v_unit = np.cross(n_unit, u_unit)
            v_norm = np.linalg.norm(v_unit)
            if v_norm <= 1e-9:
                continue
            v_unit = v_unit / v_norm
            return u_unit, v_unit

    raise ValueError("Could not build in-plane basis for wooden panel coverage.")


def descriptor(self: Block) -> str:
    """Footprint on the adhesive face plane: 1.0 x 2.0 centered at mount face center."""
    if self.start_point is None:
        return "Coverage footprint is undefined until the panel is attached to a face."

    half_span_on_face = 0.5
    half_span_adjacent = 1.0

    n_virtual = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    u_unit, v_unit = _plane_basis(plane_normal_virtual=n_virtual, rot_mat=rot_mat)

    center_virtual = np.asarray(self.start_point.center.virtual, dtype=np.float64).reshape(3)

    corner_offsets = (
        (+half_span_on_face, +half_span_adjacent),
        (+half_span_on_face, -half_span_adjacent),
        (-half_span_on_face, -half_span_adjacent),
        (-half_span_on_face, +half_span_adjacent),
    )

    corners_real: list[str] = []
    for du, dv in corner_offsets:
        corner_virtual = center_virtual + du * u_unit + dv * v_unit
        corner_real = Vector(vector=corner_virtual).real
        corners_real.append(format_float_array(arr=corner_real, precision=2))

    joined = "; ".join(corners_real)
    return f"Coverage region (four corner coordinates): {joined}"
