from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector, describe_spin
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Default spin caption plus drill tip: root face center + 3 along outward mount normal."""
    rotation_vector = self._spin_rotation_vector()
    spin_text = describe_spin(rotation_vector=rotation_vector)

    extension = 3.0
    if self.start_point is not None:
        base_virtual = np.asarray(self.start_point.center.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        base_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    normal_norm = float(np.linalg.norm(x=normal_raw))
    if normal_norm <= 1e-9:
        return f"{spin_text}\nDrill bit tip is undefined until mounted (degenerate outward axis)."
    normal_unit = normal_raw / normal_norm
    tip_virtual = base_virtual + extension * normal_unit
    tip_real = Vector(vector=tip_virtual).real
    tip_text = format_float_array(arr=tip_real, precision=2)
    return (
        f"{spin_text}\n"
        f"Drill bit tip at {tip_text} (root face center plus {extension} along the outward mount normal)."
    )
