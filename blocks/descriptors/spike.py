from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Tip lies ~2.4 units along the outward mount normal from the glue face center."""
    tip_length = 2.4
    if self.start_point is not None:
        base_virtual = np.asarray(self.start_point.center.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        base_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
        normal_raw = np.asarray(self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    normal_norm = float(np.linalg.norm(x=normal_raw))
    if normal_norm <= 1e-9:
        return "Spike tip is undefined (degenerate normal)."
    normal_unit = normal_raw / normal_norm
    tip_virtual = base_virtual + tip_length * normal_unit
    tip_real = Vector(vector=tip_virtual).real
    return (
        f"Spike tip at {format_float_array(arr=tip_real)} "
        f"({tip_length} along the outward face normal from the mount face center)."
    )
