from __future__ import annotations

from buildarena.build import Block


def descriptor(self: Block) -> str:
    """Report thrust direction from the current mount orientation."""
    if self.start_point is not None:
        thrust_vec = self.start_point.normal.vec_abs
    else:
        thrust_vec = self.geo.rotation.vec_abs
    return f"Thrust is directed away from the mount face toward {thrust_vec.caption}."
