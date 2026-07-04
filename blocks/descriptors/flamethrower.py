from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Nozzle ~2.2 along pointer axis from mount face center; aim uses pointer caption."""
    barrel_length = 2.2
    fire_vec = self._pointer_direction_vector()
    aim_caption = fire_vec.caption
    fire_unit = np.asarray(fire_vec.virtual, dtype=np.float64).reshape(3)
    fire_unit = fire_unit / max(float(np.linalg.norm(x=fire_unit)), 1e-9)
    if self.start_point is not None:
        base_virtual = np.asarray(self.start_point.center.virtual, dtype=np.float64).reshape(3)
    else:
        base_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
    nozzle_virtual = base_virtual + barrel_length * fire_unit
    nozzle_real = Vector(vector=nozzle_virtual).real
    return (
        f"Nozzle near {format_float_array(arr=nozzle_real)}; "
        f"sprays flame toward {aim_caption}."
    )
