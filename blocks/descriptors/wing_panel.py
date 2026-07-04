from __future__ import annotations

import numpy as np

from buildarena.build import Block
from buildarena.components import Vector
from buildarena.utils import format_float_array


def _pentagon_vertices_local() -> list[np.ndarray]:
    """User planform in local (span=X, chord=+Z, mid-plane Y=0)."""
    half_span = 0.75
    tri_chord = 0.75
    rect_len = 3.0
    z_rect_end = tri_chord + rect_len
    verts = (
        np.array([0.0, 0.0, 0.0], dtype=np.float64),
        np.array([half_span, 0.0, tri_chord], dtype=np.float64),
        np.array([half_span, 0.0, z_rect_end], dtype=np.float64),
        np.array([-half_span, 0.0, z_rect_end], dtype=np.float64),
        np.array([-half_span, 0.0, tri_chord], dtype=np.float64),
    )
    stacked = np.stack(arrays=verts, axis=0)
    centroid = np.mean(a=stacked, axis=0)
    return [vertex - centroid for vertex in verts]


def descriptor(self: Block) -> str:
    """Pentagon planform like Wing (id 25): vertices + chord + span + two-sided normal.

    Same flat-plate convention as the Wing block: lift/drag come from the airflow
    component along the wing-plane normal (local +Y), and both faces behave identically,
    so the normal is reported as an axis (both directions). This makes mirrored panels
    and horizontal-vs-vertical mounting unambiguous.
    """
    rot_mat = np.asarray(self.geo.rotation.rot_mat, dtype=np.float64)
    center_virtual = np.asarray(self.center_pos.virtual, dtype=np.float64).reshape(3)

    vertex_strings: list[str] = []
    for local_vertex in _pentagon_vertices_local():
        world_vertex = center_virtual + rot_mat @ local_vertex
        vertex_strings.append(format_float_array(arr=Vector(vector=world_vertex).real, precision=2))

    chord_world = rot_mat @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
    span_world = rot_mat @ np.array([1.0, 0.0, 0.0], dtype=np.float64)
    normal_world = rot_mat @ np.array([0.0, 1.0, 0.0], dtype=np.float64)

    chord_caption = Vector(vector=chord_world).caption
    span_caption = Vector(vector=span_world).caption
    normal_caption = Vector(vector=normal_world).caption
    normal_caption_opp = Vector(vector=-normal_world).caption

    vertices_joined = "; ".join(vertex_strings)
    return (
        f"Pentagonal wing planform vertices : {vertices_joined}. "
        f"Leading-to-trailing chord direction (local +Z): {chord_caption}. "
        f"Span direction (local +X): {span_caption}. "
        f"Wing-plane normal / lift-drag axis (local +Y, two-sided, same convention as Wing): "
        f"{normal_caption} and {normal_caption_opp} (the two faces are aerodynamically equivalent)."
    )
