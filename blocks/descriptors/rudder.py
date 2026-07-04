from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Report rudder tip position and pointing direction from the mount normal."""
    shaft_length = 1.8
    if self.start_point is not None:
        root_center_virtual = np.asarray(a=self.start_point.center.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(a=self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        root_center_virtual = np.asarray(a=self.center_pos.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(a=self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)

    normal_norm = float(np.linalg.norm(x=normal_raw))
    if normal_norm <= 1e-9:
        return "Rudder pointing direction is undefined (degenerate mount normal)."
    normal_unit = normal_raw / normal_norm

    tip_virtual = root_center_virtual + shaft_length * normal_unit
    tip_real = Vector(vector=tip_virtual).real
    direction_caption = Vector(vector=normal_unit).caption
    return (
        f"Rudder tip at {format_float_array(arr=tip_real, precision=2)} "
        f"(root face center plus {shaft_length} along the mount normal). "
        f"Rudder points toward {direction_caption}."
    )
