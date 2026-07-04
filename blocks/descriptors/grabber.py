from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Spherical auto-grab sensing region near the open end (+local Z face)."""
    radius = 0.2
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    normal_local = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    normal_world = rot_mat @ normal_local
    normal_unit = normal_world / max(float(np.linalg.norm(x=normal_world)), 1e-9)
    half_z = 0.5 * float(self.geo.shape.virtual[2])
    center_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)
    end_face_center_virtual = center_virtual + normal_unit * half_z
    sphere_center_real = Vector(vector=end_face_center_virtual).real
    return (
        f"Auto-grab sensing uses a sphere of radius {radius} around {format_float_array(arr=sphere_center_real)} "
        f"(near the attach end face; connectors without colliders are not valid targets)."
    )
