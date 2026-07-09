from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Compute nose/nozzle endpoints from collision-box extents and current pointer direction."""
    shape_virtual = np.asarray(a=self.geo.shape.virtual, dtype=np.float64).reshape(3)
    center_virtual = np.asarray(a=self.center_pos.virtual, dtype=np.float64).reshape(3)
    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)

    pointer_axis_local = np.asarray(a=self.pointer_axis, dtype=np.float64).reshape(3)
    pointer_norm = float(np.linalg.norm(x=pointer_axis_local))
    if pointer_norm <= 1e-9:
        return "Rocket launch direction is undefined (degenerate pointer axis)."
    pointer_axis_local = pointer_axis_local / pointer_norm

    # OBB support distance along pointer axis from box center to an end cap.
    abs_axis_local = np.array(
        object=[
            abs(float(pointer_axis_local[0])),
            abs(float(pointer_axis_local[1])),
            abs(float(pointer_axis_local[2])),
        ],
        dtype=np.float64,
    )
    half_length_along_axis = 0.5 * float(np.sum(a=abs_axis_local * shape_virtual))

    launch_axis_world = rot_mat @ pointer_axis_local
    axis_norm = float(np.linalg.norm(x=launch_axis_world))
    if axis_norm <= 1e-9:
        return "Rocket launch direction is undefined (degenerate world axis)."
    launch_axis_world = launch_axis_world / axis_norm

    nose_virtual = center_virtual + half_length_along_axis * launch_axis_world
    nozzle_virtual = center_virtual - half_length_along_axis * launch_axis_world

    nose_real = Vector(vector=nose_virtual).real
    nozzle_real = Vector(vector=nozzle_virtual).real

    return (
        f"Rocket nose-cone tip at {format_float_array(arr=nose_real, precision=2)}, "
        f"nozzle center at {format_float_array(arr=nozzle_real, precision=2)} "
        f"(computed from collision-box extents along the pointer axis). "
        f"Current launch direction: {self._pointer_direction_vector().caption}. "
        "After placement it is not rigidly welded to the surface: it freely hovers "
        "slightly above the attach face, unless held by a Grabber."
    )
