from __future__ import annotations

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Report the heating sphere center used by the torch."""
    geo = self.geo
    direction_vector_real = geo.rotation.vec_abs.real
    flame_pos = direction_vector_real + self.center_pos.real
    return f"Heating up a spherical area with radius 0.3 around {format_float_array(arr=flame_pos)}"
