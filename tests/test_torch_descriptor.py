"""Torch captions follow the measured heating offset, independently of the mount.

Run with: uv run python -m unittest discover -s tests
"""

import re
import unittest
from unittest.mock import patch

import numpy as np
import quaternion

from blocks.descriptors.torch import descriptor
from buildarena.build import Block
from buildarena.components import Face, Geometry, Orientation, Vector


# Black-box heating-bound midpoints, measured on Besiege 1.90 and 1.91.
MEASURED_HEATING_CENTER_LOCAL = np.array([0.0, 1.470720575, 0.7195732375])


class TorchDescriptorTests(unittest.TestCase):
    def setUp(self):
        # Collider loading is unrelated to captions and requires a game install.
        # Keep the real Block.shift/twist and Face/Orientation transformations.
        patcher = patch.object(Block, "init_collider")
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_torch(self, normal=(0, 0, 1), parent_rotation=(0, 0, 0)):
        parent = Geometry([0, 0, 1], [1, 1, 1], [0, 0, 0])
        parent.position = Vector([2.5, -3.25, 4.125])
        parent.rotation = Orientation(quaternion.from_rotation_vector(parent_rotation))
        mount = Face("mount", np.array(normal) * .5, normal, parent, 1, "Starting Block")
        torch = Block.__new__(Block)
        torch.geo = Geometry([0, 0, 1], [0.5, 1.5, 1], [0, 0, .75])
        torch.geo.position = Vector(mount.center.virtual.copy())
        torch.geo.rotation = Orientation(mount.normal.quat)
        torch.start_point = mount
        torch.center_offset = .75
        torch.registry_path = None
        torch.faces = {}
        torch.joint_faces = {}
        return torch

    def reported_point(self, torch):
        text = descriptor(torch)
        self.assertNotIn("sphere", text)
        self.assertNotIn("radius", text)
        match = re.search(r"centered at \[([^]]+)\]", text)
        self.assertIsNotNone(match, text)
        return np.array([float(x) for x in match[1].split(",")])

    def assert_matches_pose(self, torch):
        # An independent quaternion-vector path checks the matrix-based caption.
        expected = torch.geo.position.virtual + quaternion.rotate_vectors(
            torch.geo.rotation.quat, MEASURED_HEATING_CENTER_LOCAL
        )
        np.testing.assert_allclose(self.reported_point(torch), expected[[0, 2, 1]], atol=.00505, rtol=0)

    def test_measured_heating_center_and_extents(self):
        torch = self.make_torch()
        torch.geo.position = Vector([0, 0, 0])
        np.testing.assert_array_equal(self.reported_point(torch), [0.0, .72, 1.47])
        self.assertIn("local half-extents [0.46, 0.84, 0.39]", descriptor(torch))

    def test_cardinal_twists_move_the_heating_center(self):
        for angle, xy in ((0, (0, 1.47)), (90, (-1.47, 0)),
                          (180, (0, -1.47)), (270, (1.47, 0))):
            with self.subTest(angle=angle):
                torch = self.make_torch()
                torch.geo.position = Vector([0, 0, 0])
                torch.twist(angle)
                np.testing.assert_array_equal(self.reported_point(torch), [xy[0], .72, xy[1]])

    def test_shift_moves_the_heating_center_without_moving_the_mount(self):
        torch = self.make_torch()
        before = self.reported_point(torch)
        mount = torch.start_point.center.virtual.copy()
        shift = np.array([.25, -.5, 1.75])
        torch.shift(shift.tolist())
        np.testing.assert_allclose(self.reported_point(torch) - before, shift, atol=1e-12)
        np.testing.assert_array_equal(torch.start_point.center.virtual, mount)
        self.assert_matches_pose(torch)

    def test_all_mount_faces_twists_and_shifts(self):
        normals = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
        for normal in normals:
            for parent_rotation in ((0, 0, 0), (.3, -.45, .7)):
                for angle in (0, 45, 90, 180, 270):
                    for shift in ((0, 0, 0), (.25, -.5, 1.75)):
                        with self.subTest(normal=normal, parent_rotation=parent_rotation, angle=angle, shift=shift):
                            torch = self.make_torch(normal, parent_rotation)
                            torch.twist(angle)
                            torch.shift(shift)
                            self.assert_matches_pose(torch)

    def test_caption_uses_actual_pose_even_if_mount_position_changes(self):
        torch = self.make_torch()
        before = self.reported_point(torch)
        torch.start_point.block_geo.position = Vector([20, 30, 40])
        np.testing.assert_array_equal(self.reported_point(torch), before)
        self.assert_matches_pose(torch)


if __name__ == "__main__":
    unittest.main()
