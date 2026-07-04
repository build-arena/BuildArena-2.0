from __future__ import annotations

from buildarena.build import Block
from buildarena.components import Vector, describe_spin


def descriptor(self: Block) -> str:
    """Report the reaction-wheel axis from the current orientation."""
    if self.start_point is not None:
        axis_vec = self.start_point.normal.vec_abs
    else:
        axis_vec = self.geo.rotation.vec_abs
    positive_sense = describe_spin(rotation_vector=axis_vec)
    negative_sense = describe_spin(rotation_vector=Vector(vector=-axis_vec.virtual))
    return (
        f"Reaction-wheel control axis aligns with {axis_vec.caption}. "
        f"Positive spin: {positive_sense} Opposite spin: {negative_sense}"
    )
