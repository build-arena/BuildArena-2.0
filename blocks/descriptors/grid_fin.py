from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Report current grid-fin control-surface orientation."""
    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)
    surface_normal = rot_mat @ np.array(object=[0.0, 1.0, 0.0], dtype=np.float64)
    long_axis = rot_mat @ np.array(object=[0.0, 0.0, 1.0], dtype=np.float64)

    normal_norm = float(np.linalg.norm(x=surface_normal))
    long_axis_norm = float(np.linalg.norm(x=long_axis))
    if normal_norm <= 1e-9 or long_axis_norm <= 1e-9:
        return "Grid-fin surface orientation is undefined (degenerate rotation)."

    normal_caption = Vector(vector=surface_normal / normal_norm).caption
    long_axis_caption = Vector(vector=long_axis / long_axis_norm).caption
    return (
        f"Grid-fin airflow-facing side faces toward {normal_caption}. "
        f"The long direction of the fin surface runs toward {long_axis_caption}."
    )
