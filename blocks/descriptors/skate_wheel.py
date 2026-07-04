from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Unpowered wheelset rolling heading: perpendicular to axle line and mount normal."""
    if self.start_point is not None:
        mount_normal_raw = np.asarray(a=self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        mount_normal_raw = np.asarray(a=self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    mount_norm = float(np.linalg.norm(x=mount_normal_raw))
    if mount_norm <= 1e-9:
        return "Skate-wheel rolling direction is undefined (degenerate mount normal)."
    mount_normal_unit = mount_normal_raw / mount_norm

    rot_mat = np.asarray(a=self.geo.rotation.rot_mat, dtype=np.float64)
    shape_ext = np.asarray(a=self.geo.shape.virtual, dtype=np.float64).reshape(3)

    best_axis_world: np.ndarray | None = None
    best_score = -1.0
    for axis_idx in range(3):
        local_axis = np.zeros(shape=3, dtype=np.float64)
        local_axis[axis_idx] = 1.0
        axis_world = rot_mat @ local_axis
        axis_world_norm = float(np.linalg.norm(x=axis_world))
        if axis_world_norm <= 1e-9:
            continue
        axis_world_unit = axis_world / axis_world_norm
        in_plane_component = axis_world_unit - float(np.dot(axis_world_unit, mount_normal_unit)) * mount_normal_unit
        in_plane_norm = float(np.linalg.norm(x=in_plane_component))
        score = float(shape_ext[axis_idx]) * in_plane_norm
        if score > best_score:
            best_score = score
            best_axis_world = axis_world_unit

    if best_axis_world is None:
        return "Skate-wheel rolling direction is undefined (cannot infer axle line)."

    rolling_raw = np.cross(best_axis_world, mount_normal_unit)
    rolling_norm = float(np.linalg.norm(x=rolling_raw))
    if rolling_norm <= 1e-9:
        return "Skate-wheel rolling direction is undefined (axle parallel to mount normal)."
    rolling_unit = rolling_raw / rolling_norm

    forward_caption = Vector(vector=rolling_unit).caption
    opposite_caption = Vector(vector=-rolling_unit).caption
    return (
        f"Wheelset unpowered rolling heading (perpendicular to axle and mount normal): {forward_caption}. "
        f"Opposite heading on the same rolling line: {opposite_caption}."
    )
