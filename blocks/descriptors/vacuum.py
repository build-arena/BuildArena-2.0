from __future__ import annotations

from buildarena.build import Block
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Water-cannon-style inlet/nozzle coordinates, but for suction direction."""
    geo = self.geo
    pos = self.center_pos.real
    vec = geo.rotation.vec_abs.real
    nozzle_pos = pos + vec * 1.0
    inlet_pos = pos - vec * 0.75
    return (
        f"Inlet is at {format_float_array(arr=inlet_pos)}, "
        f"nozzle is at {format_float_array(arr=nozzle_pos)}, "
        f"sucks toward {geo.rotation.caption}"
    )
