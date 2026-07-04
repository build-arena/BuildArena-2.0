from __future__ import annotations

from buildarena.build import Block


def descriptor(self: Block) -> str:
    """Pointer-style fire direction caption for harpoon launch."""
    fire_direction = self._pointer_direction_vector().caption
    return f"Fires harpoons toward {fire_direction}."
