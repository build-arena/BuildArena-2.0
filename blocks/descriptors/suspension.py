from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Spring axis along the mount face normal: compression −n, extension +n."""
    if self.start_point is not None:
        axis_virtual = np.asarray(self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        axis_virtual = np.asarray(self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    axis_norm = float(np.linalg.norm(x=axis_virtual))
    if axis_norm <= 1e-9:
        return "Suspension axis is undefined (degenerate normal)."
    axis_unit = axis_virtual / axis_norm
    extend_vec = Vector(vector=axis_unit)
    compress_vec = Vector(vector=-axis_unit)
    return (
        f"Suspension axis: compresses toward {compress_vec.caption} (negative face normal), "
        f"extends toward {extend_vec.caption} (positive face normal)."
    )
