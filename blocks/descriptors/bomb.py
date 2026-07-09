from __future__ import annotations

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Bomb center and mount behavior for payload placement planning."""
    center_real = self.center_pos.real
    return (
        f"Sphere center at {format_float_array(arr=center_real)} (same as the Position line). "
        "After placement it is not rigidly welded to the surface: it freely hovers "
        "slightly above the attach face by about 0.2 units, unless held by a Grabber."
    )
