from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Describe double-edged blade orientation from current block rotation.

    Local block axes from registry shape (~width X, thickness Y, length Z):
    cutting-edge outward directions are ±local X in world space.
    """
    rot_mat = self.geo.rotation.rot_mat
    edge_pos_local = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    edge_neg_local = np.array([-1.0, 0.0, 0.0], dtype=np.float64)
    edge_pos_world = rot_mat @ edge_pos_local
    edge_neg_world = rot_mat @ edge_neg_local
    cap_pos = Vector(vector=edge_pos_world).caption
    cap_neg = Vector(vector=edge_neg_world).caption
    return (
        f"Blade edge orientations are {cap_pos} and {cap_neg} "
        f"(symmetric double edge; twist on the root face changes these headings)."
    )
