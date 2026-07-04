from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector, describe_spin


def descriptor(self: Block) -> str:
    """Unpowered wheel spin caption using install-axis free-rotation convention."""
    if self.start_point is not None:
        axis_virtual = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        axis_virtual = np.asarray(self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    axis_norm = float(np.linalg.norm(x=axis_virtual))
    if axis_norm <= 1e-9:
        return "Wheel rotation axis is undefined (degenerate normal)."
    axis_unit = axis_virtual / axis_norm
    positive_sense = describe_spin(rotation_vector=Vector(vector=axis_unit))
    negative_sense = describe_spin(rotation_vector=Vector(vector=-axis_unit))
    return (
        f"Unpowered free rotation about the install axis: {positive_sense} "
        f"Opposite sense: {negative_sense}"
    )
