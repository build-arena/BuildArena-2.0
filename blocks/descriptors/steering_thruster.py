from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Report the opposed side-nozzle thrust axis from the current orientation."""
    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)
    side_axis = rot_mat @ np.array(object=[0.0, 1.0, 0.0], dtype=np.float64)
    side_norm = float(np.linalg.norm(x=side_axis))
    if side_norm <= 1e-9:
        return "RCS side-nozzle axis is undefined (degenerate orientation)."
    side_unit = side_axis / side_norm
    positive_vec = Vector(vector=side_unit)
    negative_vec = Vector(vector=-side_unit)
    return (
        f"Opposed RCS nozzles thrust along {positive_vec.caption} "
        f"and {negative_vec.caption}."
    )
