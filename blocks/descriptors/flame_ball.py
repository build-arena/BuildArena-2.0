from __future__ import annotations

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Same center reporting style as Bomb, plus flame influence sphere."""
    influence_radius = 1.5
    center_real = self.center_pos.real
    return (
        f"Sphere center at {format_float_array(arr=center_real)} (same as the Position line). "
        f"Continuous flame fills a spherical influence region of radius {influence_radius} around that center; water can extinguish it. "
        "Like the bomb, it is not rigidly welded to the surface and rests "
        "slightly separated from the attach face, unless held by a Grabber."
    )
