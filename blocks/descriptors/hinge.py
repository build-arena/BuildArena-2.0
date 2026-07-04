from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Passive free hinge: low-friction swing limits (same axis model as steering hinge).

    The attachable faces are laid out like a cube after mounting: four side faces
    at the hinge body midpoint and one free end cap. The hinge pin is taken as
    local +X, so swing motion lies in the local YZ plane. The two limit headings
    use ±local Y in world space (symmetric about the pin).
    """
    rot_mat = self.geo.rotation.rot_mat
    swing_pos_local = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    swing_neg_local = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    swing_pos_world = rot_mat @ swing_pos_local
    swing_neg_world = rot_mat @ swing_neg_local
    cap_pos = Vector(vector=swing_pos_world).caption
    cap_neg = Vector(vector=swing_neg_world).caption
    return (
        f"Passive low-friction hinge; swing limits toward {cap_pos} and toward {cap_neg} "
        f"(pin along local +X from dump layout)."
    )
