from __future__ import annotations

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Same center and mount caption style as Flame Ball; geometry is a solid stone sphere."""
    stone_radius = 0.5
    center_real = self.center_pos.real
    return (
        f"Sphere center at {format_float_array(arr=center_real)} (same as the Position line). "
        f"Solid stone ball of diameter 1 (radius {stone_radius}) around that center; violent impacts can shatter it into fragments. "
        "Like the flame ball, it is not rigidly welded to the surface and rests "
        "slightly separated from the attach face, unless held by a Grabber."
    )
