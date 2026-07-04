from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Barrel breech/muzzle along pointer axis; aim uses the same orientation caption as other pointers."""
    fire_vec = self._pointer_direction_vector()
    fire_unit_real = fire_vec.real / max(float(np.linalg.norm(x=fire_vec.real)), 1e-9)
    pos = self.center_pos.real
    breech_pos = pos - fire_unit_real * 1.0
    muzzle_pos = pos + fire_unit_real * 1.0
    aim_caption = fire_vec.caption
    return (
        f"Breech end near {format_float_array(arr=breech_pos)}, muzzle near {format_float_array(arr=muzzle_pos)}, "
        f"fires toward {aim_caption}."
    )
