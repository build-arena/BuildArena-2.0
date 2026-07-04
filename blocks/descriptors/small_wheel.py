from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Caster wheel center: root face center + 0.75 along outward mount normal."""
    extension = 0.75
    if self.start_point is not None:
        base_virtual = np.asarray(self.start_point.center.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        base_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    normal_norm = float(np.linalg.norm(x=normal_raw))
    if normal_norm <= 1e-9:
        return "Small wheel center is undefined (degenerate outward axis)."
    normal_unit = normal_raw / normal_norm
    wheel_center_virtual = base_virtual + extension * normal_unit
    wheel_center_real = Vector(vector=wheel_center_virtual).real
    return (
        f"Small caster wheel center at {format_float_array(arr=wheel_center_real, precision=2)} "
        f"(root face center plus {extension} along the outward mount normal)."
    )
