from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Powered steering hinge with one attachable face on the free end cap."""
    rot_mat = self.geo.rotation.rot_mat
    swing_pos_local = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    swing_neg_local = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    swing_pos_world = rot_mat @ swing_pos_local
    swing_neg_world = rot_mat @ swing_neg_local
    cap_pos = Vector(vector=swing_pos_world).caption
    cap_neg = Vector(vector=swing_neg_world).caption
    return (
        f"Powered steering hinge: active swing toward {cap_pos} and toward {cap_neg} "
        f"(pin along local +X from dump layout)."
    )
