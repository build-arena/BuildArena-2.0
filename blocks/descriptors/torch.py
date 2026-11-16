from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


# Approximate heating bounds measured with water cannons in Besiege 1.90/1.91.
# These offsets use the torch's local Unity axes, before conversion to ENU.
HEATING_CENTER_LOCAL = np.array([0.0, 1.4707, 0.7196], dtype=np.float64)
HEATING_HALF_EXTENTS_LOCAL = np.array([0.4642, 0.8355, 0.3868], dtype=np.float64)


def descriptor(self: Block) -> str:
    """Report the measured heating box in the block's actual pose."""
    geo = self.geo
    heating_center = Vector(geo.position.virtual + geo.rotation.rot_mat @ HEATING_CENTER_LOCAL)
    return (
        f"Heating an approximately box-shaped area centered at {format_float_array(arr=heating_center.real)}, "
        f"with local half-extents {format_float_array(arr=HEATING_HALF_EXTENTS_LOCAL)} "
        "along the torch's local X/Y/Z axes (rotates with the torch)."
    )
