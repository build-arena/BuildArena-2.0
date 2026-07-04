from __future__ import annotations

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Balloon body center matches block center used in Position."""
    center_real = self.center_pos.real
    return (
        f"Lift balloon sphere center at {format_float_array(arr=center_real)} (same as the Position line)."
    )
