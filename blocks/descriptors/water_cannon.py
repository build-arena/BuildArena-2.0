from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    spray_vec = self._pointer_direction_vector()
    spray_unit_real = spray_vec.real / max(float(np.linalg.norm(x=spray_vec.real)), 1e-9)
    pos = self.center_pos.real
    inlet_pos = pos - spray_unit_real * 0.75
    outlet_pos = pos + spray_unit_real * 1.0
    return (
        f"Inlet is at {format_float_array(arr=inlet_pos)}, "
        f"outlet is at {format_float_array(arr=outlet_pos)}, "
        f"sprays towards {spray_vec.caption}"
    )
