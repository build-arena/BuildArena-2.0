from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


TANK_CENTER_LOCAL = np.array([0.0, 0.0, 0.5], dtype=np.float64)
MUZZLE_OFFSET = 2.3


def descriptor(self: Block) -> str:
    """Report fuel-cannon tank center, muzzle, and firing direction."""
    fire_vec = self._pointer_direction_vector()
    fire_axis = np.asarray(a=fire_vec.virtual, dtype=np.float64).reshape(3)
    fire_norm = float(np.linalg.norm(x=fire_axis))
    if fire_norm <= 1e-9:
        return "Fuel-cannon firing direction is undefined (degenerate pointer axis)."
    fire_unit = fire_axis / fire_norm

    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)
    center_virtual = np.asarray(a=self.center_pos.virtual, dtype=np.float64).reshape(3)
    tank_center_virtual = center_virtual + rot_mat @ TANK_CENTER_LOCAL
    muzzle_virtual = tank_center_virtual + MUZZLE_OFFSET * fire_unit

    return (
        f"Fuel tank center at {format_float_array(arr=Vector(vector=tank_center_virtual).real, precision=2)}, "
        f"muzzle near {format_float_array(arr=Vector(vector=muzzle_virtual).real, precision=2)}. "
        f"Fires fuel-powered fireballs toward {fire_vec.caption}."
    )
