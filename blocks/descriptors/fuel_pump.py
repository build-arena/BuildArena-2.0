from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Report the two side source faces and the free-end outlet face."""
    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)
    center_virtual = np.asarray(a=self.center_pos.virtual, dtype=np.float64).reshape(3)
    half = 0.5 * np.asarray(a=self.geo.shape.virtual, dtype=np.float64).reshape(3)

    def place(*, local_offset: np.ndarray, local_normal: np.ndarray) -> tuple[str, str] | None:
        normal_world = rot_mat @ local_normal
        normal_norm = float(np.linalg.norm(x=normal_world))
        if normal_norm <= 1e-9:
            return None
        world = center_virtual + rot_mat @ local_offset
        position = format_float_array(arr=Vector(vector=world).real, precision=2)
        facing = Vector(vector=normal_world / normal_norm).caption
        return position, facing

    side_neg = place(
        local_offset=np.array(object=[-half[0], 0.0, 0.0], dtype=np.float64),
        local_normal=np.array(object=[-1.0, 0.0, 0.0], dtype=np.float64),
    )
    side_pos = place(
        local_offset=np.array(object=[half[0], 0.0, 0.0], dtype=np.float64),
        local_normal=np.array(object=[1.0, 0.0, 0.0], dtype=np.float64),
    )
    end = place(
        local_offset=np.array(object=[0.0, 0.0, half[2]], dtype=np.float64),
        local_normal=np.array(object=[0.0, 0.0, 1.0], dtype=np.float64),
    )
    if side_neg is None or side_pos is None or end is None:
        return "Fuel-pump face geometry is undefined (degenerate rotation)."
    return (
        f"Side fuel-source face at {side_neg[0]}, facing {side_neg[1]}. "
        f"Opposite side fuel-source face at {side_pos[0]}, facing {side_pos[1]}. "
        f"Free-end fuel-outlet face at {end[0]}, facing {end[1]}. "
        "Fuel is drawn from the mounted fuel system and these two side faces, "
        "and pumped into the fuel system on the free end face."
    )
