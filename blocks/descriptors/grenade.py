from __future__ import annotations

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Bomb-style center and mount caption for the grenade sphere."""
    center_real = self.center_pos.real
    return (
        f"Sphere center at {format_float_array(arr=center_real)} (same as the Position line). "
        "Like the bomb, it is not rigidly welded to the surface and rests slightly separated from the attach face."
    )
