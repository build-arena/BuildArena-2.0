from __future__ import annotations

from buildarena.build import Block


def descriptor(self: Block) -> str:
    """Pointer-style firing caption for crossbow bolts."""
    fire_direction = self._pointer_direction_vector().caption
    return f"Fires crossbow bolts toward {fire_direction}."
