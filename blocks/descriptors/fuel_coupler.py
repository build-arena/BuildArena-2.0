from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Report the grab/re-dock sensing region near the coupler end face."""
    radius = 0.2
    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)
    normal_local = np.array(object=[0.0, 0.0, 1.0], dtype=np.float64)
    normal_world = rot_mat @ normal_local
    normal_unit = normal_world / max(float(np.linalg.norm(x=normal_world)), 1e-9)
    half_z = 0.5 * float(self.geo.shape.virtual[2])
    center_virtual = np.asarray(a=self.center_pos.virtual, dtype=np.float64).reshape(3)
    end_face_center_virtual = center_virtual + normal_unit * half_z
    sphere_center_real = Vector(vector=end_face_center_virtual).real
    return (
        f"Fuel-coupler grab/re-dock sensing uses a sphere of radius {radius} around "
        f"{format_float_array(arr=sphere_center_real)} near the coupler end face; "
        "fuel transfer requires fuel-flow-capable faces on both sides."
    )
