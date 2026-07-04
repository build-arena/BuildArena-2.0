from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector


def descriptor(self: Block) -> str:
    """Report the current opening direction of the hemispherical bowl."""
    if self.start_point is not None:
        opening_raw = np.asarray(a=self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
    else:
        opening_raw = np.asarray(a=self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)
    opening_norm = float(np.linalg.norm(x=opening_raw))
    if opening_norm <= 1e-9:
        return "Bowl opening direction is undefined (degenerate orientation)."
    opening_unit = opening_raw / opening_norm
    opening_caption = Vector(vector=opening_unit).caption
    return f"Bowl opening currently faces toward {opening_caption}."
