from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def descriptor(self: Block) -> str:
    """Bite direction is opposite the mount-face normal; report that plus front tooth-tip point."""
    jaw_length = 1.5
    center_virtual = np.asarray(a=self.center_pos.virtual, dtype=np.float64).reshape(3)

    if self.start_point is not None:
        mount_center_virtual = np.asarray(a=self.start_point.center.virtual, dtype=np.float64).reshape(3)
        mount_normal_virtual = np.asarray(a=self.start_point.normal.vec_abs.virtual, dtype=np.float64).reshape(3)
        bite_axis_raw = -mount_normal_virtual
    else:
        mount_center_virtual = None
        bite_axis_raw = -np.asarray(a=self.geo.rotation.vec_abs.virtual, dtype=np.float64).reshape(3)

    bite_axis_norm = float(np.linalg.norm(x=bite_axis_raw))
    if bite_axis_norm <= 1e-9:
        return "Jaw bite direction is undefined (degenerate mount-face normal)."
    bite_axis_unit = bite_axis_raw / bite_axis_norm

    if mount_center_virtual is not None:
        tooth_tip_virtual = mount_center_virtual + jaw_length * bite_axis_unit
    else:
        tooth_tip_virtual = center_virtual + 0.5 * jaw_length * bite_axis_unit

    tooth_tip_real = Vector(vector=tooth_tip_virtual).real
    tooth_tip_text = format_float_array(arr=tooth_tip_real, precision=2)
    bite_direction_caption = Vector(vector=bite_axis_unit).caption

    return (
        f"Jaw bite-motion direction (opposite the attach-face normal): {bite_direction_caption}. "
        f"Front tooth tip at {tooth_tip_text}."
    )
