from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Upper hemispherical cage center: mount face center + 1.5 along outward normal."""
    extension = 1.5
    if self.start_point is not None:
        base_virtual = np.asarray(self.start_point.center.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        base_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    normal_norm = float(np.linalg.norm(x=normal_raw))
    if normal_norm <= 1e-9:
        return "Upper cage center is undefined (degenerate normal)."
    normal_unit = normal_raw / normal_norm
    sphere_center_virtual = base_virtual + extension * normal_unit
    sphere_center_real = Vector(vector=sphere_center_virtual).real
    return (
        f"Upper hemispherical cage center at {format_float_array(arr=sphere_center_real)} "
        f"({extension} along the outward mount normal from the root face center)."
    )
