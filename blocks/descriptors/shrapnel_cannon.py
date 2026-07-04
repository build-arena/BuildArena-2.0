from __future__ import annotations

from buildarena.build import Block


def descriptor(self: Block) -> str:
    """Report firing direction using the same orientation convention as water_cannon."""
    geo = self.geo
    return f"Fires shrapnel towards {geo.rotation.caption}"
