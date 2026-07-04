from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Small-propeller specific blade angle and tip coordinate."""
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    half_shape = np.asarray(self.geo.shape.virtual, dtype=np.float64).reshape(3) * 0.5
    hz = float(half_shape[2])
    center_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)

    blade_axis_local = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    blade_axis_virtual = rot_mat @ blade_axis_local
    blade_axis_real = np.asarray(Vector(vector=blade_axis_virtual).real, dtype=np.float64).reshape(3)
    axis_norm = float(np.linalg.norm(x=blade_axis_real))
    if axis_norm <= 1e-9:
        return "Small propeller blade axis is degenerate."
    blade_unit_real = blade_axis_real / axis_norm

    axis_horizontal = np.array([blade_unit_real[0], blade_unit_real[1], 0.0], dtype=np.float64)
    horizontal_norm = float(np.linalg.norm(x=axis_horizontal))
    if horizontal_norm < 1e-6:
        horizontal_angle_text = "undefined (blade axis nearly vertical)"
    else:
        axis_h_unit = axis_horizontal / horizontal_norm
        ref_forward = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        dot_f = float(np.dot(axis_h_unit, ref_forward))
        cross_f = float(axis_h_unit[0] * ref_forward[1] - axis_h_unit[1] * ref_forward[0])
        angle_deg = float(np.degrees(np.arctan2(cross_f, dot_f)))
        horizontal_angle_text = f"{angle_deg:.1f}° from forward (horizontal projection)"

    tip_virtual = center_virtual + blade_axis_virtual * hz
    tip_real = Vector(vector=tip_virtual).real
    tip_text = format_float_array(arr=tip_real, precision=2)

    return (
        f"Small-propeller blade horizontal angle of attack (chord projection vs forward): {horizontal_angle_text}. "
        f"Outboard tip (local +Z end) at {tip_text}."
    )
