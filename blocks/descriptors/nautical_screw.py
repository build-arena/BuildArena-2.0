from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector, describe_spin


def descriptor(self: Block) -> str:
    """Right-hand screw: advance/thrust follows +rotation-vector direction."""
    rotation_vector = self._spin_rotation_vector()
    spin_text = describe_spin(rotation_vector=rotation_vector)

    rot_virtual = np.asarray(a=rotation_vector.virtual, dtype=np.float64).reshape(3)
    rot_norm = float(np.linalg.norm(x=rot_virtual))
    if rot_norm <= 1e-9:
        return f"{spin_text}\nScrew advance direction is undefined (degenerate spin axis)."
    advance_unit = rot_virtual / rot_norm
    advance_caption = Vector(vector=advance_unit).caption
    return (
        f"{spin_text}\n"
        f"Right-hand screw advance direction: {advance_caption}. "
        f"If submerged, axial thrust is toward this same direction."
    )
