from __future__ import annotations

from buildarena.build import Block


def descriptor(self: Block) -> str:
    """Lift is along the install-face normal (thrust perpendicular to the glued face)."""
    if self.start_point is not None:
        lift_vec = self.start_point.normal.vec_abs
    else:
        lift_vec = self.geo.rotation.vec_abs
    return (
        "Directed lift is perpendicular to the install face and aligns with "
        f"{lift_vec.caption}."
    )
